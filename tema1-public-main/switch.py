#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name
mac_table = {}
ports = {}

def parse_ethernet_header(data):
    # Unpack the header fields from the byte array
    #dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
    dest_mac = data[0:6]
    src_mac = data[6:12]
    
    # Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
    ether_type = (data[12] << 8) + data[13]

    vlan_id = -1
    # Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
    if ether_type == 0x8200:
        vlan_tci = int.from_bytes(data[14:16], byteorder='big')
        vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
        ether_type = (data[16] << 8) + data[17]

    return dest_mac, src_mac, ether_type, vlan_id

def create_vlan_tag(vlan_id):
    # 0x8100 for the Ethertype for 802.1Q
    # vlan_id & 0x0FFF ensures that only the last 12 bits are used
    return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)

def send_bdpu_every_sec():
    while True:
        # TODO Send BDPU every second if necessary
        time.sleep(1)

def modify_data(data, interface1, interface2, vlan_id, length):
    vlan_ok = vlan_id
    if vlan_ok == -1:
        vlan_ok = ports[interface1]
    if ports[interface1] == -1:
        if ports[interface2] != -1:
            data = data[0:12] + data[16:]
            length = length - 4
    if ports[interface1] != -1:
        if ports[interface2] == -1:
            data = data[0:12] + create_vlan_tag(vlan_ok) + data[12:]
            length = length + 4
    return data, length

def check_tag(data, length, in_interface, out_interface, vlan_id):
    vlan_in = ports[get_interface_name(in_interface)]
    vlan_out = ports[get_interface_name(out_interface)]
    # trunk - access
    if vlan_in == -1 and vlan_out != -1:
        data = data[0:12] + data[16:]
        length = length - 4
    else:
        # access - trunk
        if vlan_in != -1 and vlan_out == -1:
            vlan_ok = vlan_id
            if vlan_ok == -1:
                vlan_ok = vlan_in
            data = data[0:12] + create_vlan_tag(vlan_ok) + data[12:]
            length = length + 4
    return data, length

def ok_to_send(in_interface, out_interface, vlan_id):
    vlan_in = ports[get_interface_name(in_interface)]
    vlan_out = ports[get_interface_name(out_interface)]
    if vlan_out != -1 and vlan_out != vlan_id:
        return False
    return True

def check_different_vlans(vlan_id, out_interface, in_interface):
    vlan_out = ports[get_interface_name(out_interface)]
    vlan_in = ports[get_interface_name(in_interface)]
    vlan_ok = vlan_id
    if vlan_ok == -1:
        vlan_ok = vlan_in
    if vlan_out != -1 and vlan_out != vlan_ok:
        return False
    return True
            
        


def main():
    # init returns the max interface number. Our interfaces
    # are 0, 1, 2, ..., init_ret value + 1
    switch_id = sys.argv[1]

    num_interfaces = wrapper.init(sys.argv[2:])
    interfaces = range(0, num_interfaces)

    print("# Starting switch with id {}".format(switch_id), flush=True)
    print("[INFO] Switch MAC", ':'.join(f'{b:02x}' for b in get_switch_mac()))

    # Create and start a new thread that deals with sending BDPU
    t = threading.Thread(target=send_bdpu_every_sec)
    t.start()

    # Printing interface names
    for i in interfaces:
        print(get_interface_name(i))

    # f = open("configs/switch" + switch_id + ".cfg", "r")
    # switch_prio = f.readline()
    # while True:
    #     line = f.readline()
    #     if not line:
    #         break
    #     words = line.strip().split(' ')
    #     if words[1] != "T":
    #         ports[words[0]] = int(words[1])
    #     else:
    #         ports[words[0]] = -1
    # f.close()

    while True:
        # Note that data is of type bytes([...]).
        # b1 = bytes([72, 101, 108, 108, 111])  # "Hello"
        # b2 = bytes([32, 87, 111, 114, 108, 100])  # " World"
        # b3 = b1[0:2] + b[3:4].
        interface, data, length = recv_from_any_link()

        dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)

        # Print the MAC src and MAC dst in human readable format
        dest_mac = ':'.join(f'{b:02x}' for b in dest_mac)
        src_mac = ':'.join(f'{b:02x}' for b in src_mac)

        # Note. Adding a VLAN tag can be as easy as
        # tagged_frame = data[0:12] + create_vlan_tag(10) + data[12:]

        print(f'Destination MAC: {dest_mac}')
        print(f'Source MAC: {src_mac}')
        print(f'EtherType: {ethertype}')

        print("Received frame of size {} on interface {}".format(length, interface), flush=True)

        # TODO: Implement forwarding with learning
        mac_table[src_mac] = interface
        leastSignificantBit = int(dest_mac.split(':')[0], 16)
        # unicast - 0 | multicast - 1
        if leastSignificantBit & 1 == 0:
            if dest_mac in mac_table:
                if ok_to_send(interface, mac_table[dest_mac], vlan_id):
                    data, length = check_tag(data, length, interface, mac_table[dest_mac], vlan_id)
                    send_to_link(mac_table[dest_mac], data, length)
            else:
                for i in interfaces:
                    if i != interface:
                        if ok_to_send(interface, i, vlan_id):
                            data, length = check_tag(data, length, interface, i, vlan_id)
                            send_to_link(i, data, length)
        else:
            for i in interfaces:
                if i != interface:
                    if ok_to_send(interface, i, vlan_id):
                        data, length = check_tag(data, length, interface, i, vlan_id)
                        send_to_link(i, data, length)
        # TODO: Implement VLAN support
        
        

        



        # TODO: Implement STP support

        # data is of type bytes.
        # send_to_link(i, data, length)

if __name__ == "__main__":
    main()
