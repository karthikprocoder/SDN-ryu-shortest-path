from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_0
from ryu.lib.mac import haddr_to_bin
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.topology import event 
from ryu.topology.api import get_host
from ryu.controller import ofp_event

import networkx as nx



class SDNController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDNController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # switch, dst => out_port
        self.topology_api_app = self
        self.hosts = []
        self.switches = {}
        self.net = nx.DiGraph()

    def add_flow(self, datapath, in_port, dst, src, actions):
        ofproto = datapath.ofproto

        match = datapath.ofproto_parser.OFPMatch(
            in_port=in_port,
            dl_dst=haddr_to_bin(dst), dl_src=haddr_to_bin(src))

        mod = datapath.ofproto_parser.OFPFlowMod(
            datapath=datapath, match=match, cookie=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
            priority=ofproto.OFP_DEFAULT_PRIORITY,
            flags=ofproto.OFPFF_SEND_FLOW_REM, actions=actions)
        
        datapath.send_msg(mod)

        print(f"Flow added to s{datapath.id}")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        dst = eth.dst
        src = eth.src

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        # self.logger.info("packet in %s %s %s %s", dpid, src, dst, msg.in_port)

        self.mac_to_port[dpid][src] = msg.in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            self.add_flow(datapath, msg.in_port, dst, src, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=msg.in_port,
            actions=actions, data=data)
        datapath.send_msg(out)



    @set_ev_cls(event.EventHostAdd, MAIN_DISPATCHER)
    def host_add_handler(self, ev):
        # Host is added to the network
        # self.hosts.append(ev.host)
        # self.host_to_switch[ev.host.mac] = ev.host.port.dpid
        # print("Host MAC Address:", ev.host.mac)
        # print("connected to switch: ", ev.host.port.dpid)
        # self.net.add_node(ev.host.mac)
        self.precompute_flow_tables()

    @set_ev_cls(event.EventSwitchEnter, MAIN_DISPATCHER)
    def switch_add_handler(self, ev):
        self.switches[ev.switch.dp.id]  = ev.switch.dp
        self.net.add_node(ev.switch.dp.id)
        # print("Switch added: ", ev.switch)

    @set_ev_cls(event.EventLinkAdd, MAIN_DISPATCHER)
    def link_add_handler(self, ev):
        link = ev.link
        self.net.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no)
        self.net.add_edge(link.dst.dpid, link.src.dpid, port=link.dst.port_no)
        self.precompute_flow_tables()

    @set_ev_cls(event.EventLinkDelete, MAIN_DISPATCHER)
    def link_del_handler(self, ev):
        # self.print_switch()
        link = ev.link
        try:
            self.net.remove_edge(link.src.dpid, link.dst.dpid)
            self.net.remove_edge(link.dst.dpid, link.src.dpid)
        except nx.NetworkXException as e:
            pass
        self.precompute_flow_tables()

    def print_network(self):
        print("Hosts to switches ")
        for h in self.hosts:
            print(f"{h.mac} -> s{h.port.dpid} at port {h.port.port_no}")
        print("Switches to Switches ")
        for u, v, data in self.net.edges(data=True):
            print(f"Link: {u} -> {v}, Port: {data['port']}")

    def print_switch(self):
        for u, v, data in self.net.edges(data=True):
            print(f"Link: {u} -> {v}, Port: {data['port']}")  

    def install_path(self, path, h1, h2):
        # print(f"path from {h1.mac} -> {h2.mac} is {path}")
        for i in range(len(path)):
            dp = self.switches[path[i]]
            in_port, out_port = -1, -1
            if i == 0:
                in_port = h1.port.port_no
                # print("in_port: ", in_port)
            if i == len(path) - 1:
                out_port = h2.port.port_no
                # print("out_port: ", out_port)
            if i > 0:
                prev_dp = self.switches[path[i - 1]]
                in_port = self.net[dp.id][prev_dp.id]['port']
                # print("in_port: ", in_port)
            if i < len(path) - 1:
                nxt_dp = self.switches[path[i + 1]]
                out_port = self.net[dp.id][nxt_dp.id]['port']
                # print("out_port: ", out_port)
            self.mac_to_port[path[i]][h2.mac] = out_port
            actions = [dp.ofproto_parser.OFPActionOutput(out_port)]
            self.add_flow(datapath=dp, in_port=in_port, src=h1.mac, dst=h2.mac, actions=actions)
                
        
    def precompute_flow_tables(self):
        # Precompute shortest paths and update flow tables for switches
        nodes_list = list(self.net.nodes)
        # print(nodes_list)
        # print("Hosts: ", self.hosts)
        self.hosts = get_host(self)
        for h1 in self.hosts:
            for h2 in self.hosts:
                m1, m2 = h1.mac, h2.mac
                s1, s2 = h1.port.dpid, h2.port.dpid
                # sp1, sp2 = h1.port.port_no, h2.port.port_no
                if m1 == m2:
                    continue
                # print(s1, s2)
                try:
                    path = nx.shortest_path(self.net, source=s1, target=s2)
                    self.install_path(path, h1, h2)
                except nx.NetworkXNoPath as e:
                    pass

