#!/usr/bin/env python2
#client.py

import sys
import time
import socket
import match_pb2
from urlparse import urlparse

from serialization import PB2Interface

class ConnectionClosed(Exception):
    pass

class ProtocolError(Exception):
    pass

def netstring(s=''):
    return str(len(s)) + ":" + s + ","

class MatchRunner():
    def __init__(self, fname):
        self.state = 'DISCONNECTED'
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #self.socket.setblocking(0)
        self.oldbuf = ""
        self.serializer = PB2Interface()
        self.states = {} # turn -> gamestate (for history)
    
        self.fname = fname
    
    def send(self, data):
        # print "Sending:", repr(data)
        self.socket.sendall(netstring(data))
        
    def recv(self):
        suffix_length = 1
        min_prefix_length = 2
        bufsize = 2048
        s = ""
        data = self.oldbuf
        data_len_to_receive = len(netstring())
        
        n_chunks = 0
        while len(data) < data_len_to_receive or n_chunks == 0:
            if len(data) < data_len_to_receive:
                try:
                    chunk = self.socket.recv(bufsize)
                except socket.error:
                    raise ConnectionClosed
                if chunk == '':
                    raise ConnectionClosed
                data += chunk
            if n_chunks == 0:
                i = data.find(':')
                string_length = int(data[:i])
                prefix_length = i+1
                data_len_to_receive = prefix_length+string_length+suffix_length
            n_chunks += 1
        
        self.oldbuf = data[data_len_to_receive:]
        if self.oldbuf:
            print "data:", repr(data[prefix_length:prefix_length+string_length]), "oldbuf:",repr(self.oldbuf)
            raise ProtocolError("Multiple messages received")
        
        return data[prefix_length:prefix_length+string_length]
    
    
    def disconnect(self):
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close() 
    
    def exit(self):
        self.disconnect()
        sys.exit(1)
    
    def join_or_create_match(self, uri_str):
        uri = urlparse(uri_str)
        host, port = uri.netloc.split(':')
        self.socket.connect((host, int(port)))
        
        i = 0
        while True:
            try:
                msg = self.recv()
            except ConnectionClosed:
                print "Connection closed"
                if self.state != 'ENDED':
                    sys.exit(1)
                else:
                    pass
            spl = msg.split(' ')
            self.state = spl[0]
            msg2 = ' '.join(spl[1:])
            
            #print repr(msg)
            
            if self.state == 'CONNECTED':
                if i == 0:
                    self.send('NAME {}'.format(self.fname))
                
                elif i > 1: #by message #2 we should be joined
                    print "Joining match failed."
                    self.exit()
                
                elif uri.path[1:]:
                    print "Joining match {}...".format(uri_str)
                    self.send('JOIN {}'.format(uri.path[1:]))
                else:
                    print "Creating match..."
                    self.send('CREATE num_players=2')
            elif self.state == 'JOINED':
                self.match_uri = msg2.split(' ')[0]
                print "Joined {}".format(msg2)
                
                time.sleep(1)
                self.send('START')
            elif self.state == 'STARTED':
                sett, self.player_id = self.serializer.deserialize_settings(msg2)
                # TODO: actually apply settings
                
                self.send('TURN')
            elif self.state == 'TURN':
                # decode state...
                gs = self.serializer.deserialize_gamestate(msg2)
                self.states[gs.turn] = gs
                print "Running turn {}".format(gs.turn)
                
                
                actions = {}; bots = {}
                # calculate actions
                #time.sleep(0.1) #mediumslowbot
                print gs.robots
                for loc, bot in gs.robots.items():
                    if bot['player_id'] == self.player_id:
                        actions[loc] = ('GUARD',)
                        bots[loc] = bot
                
                #send actions
                actions_str = self.serializer.serialize_actions(
                    actions, bots, turn=gs.turn)
                self.send('TURN {}'.format(actions_str))
            elif self.state == 'ENDED':
                gs = self.serializer.deserialize_gamestate(msg2)
                self.states[gs.turn] = gs
                self.do_end_stuff()
                break
            i += 1
    
    def do_end_stuff(self):
        print "Game finished sucessfully, Score: ? ?:? ?"



if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Run a robotgame match over the network")
    parser.add_argument('uri', type=str, help="URI of match (join) or server (create)")
    parser.add_argument('robot', type=str, help="Filename of the robot to use")
    args = parser.parse_args()
        
    mr = MatchRunner(fname=args.robot)
    mr.join_or_create_match(args.uri)
