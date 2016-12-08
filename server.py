#!/usr/bin/env python2
#server.py

# protocol roughly:
#  server idle.
#  player joins. server sends game config. [*N]
#  player count = game config layer count -> game start
#  game: [*N]
#   server sends state to clients
#   clients send actions to server

import random
import string
import re
import shlex

from twisted.protocols.basic import NetstringReceiver
from twisted.internet.protocol import Protocol, Factory
from twisted.protocols.policies import TimeoutMixin
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.internet import reactor

from rgkit.game import Game, settings
from rgkit.gamestate import GameState
from serialization import PB2Interface as SerializationInterface
import match_pb2


host = "127.0.0.1"
port = 8007

timeout_in_s = 65536
   
matches = {}  # game_id -> game

id_charset = string.ascii_letters + string.digits

join_re = re.compile(r"(?<=^JOIN )[{}]+$".format(id_charset))
players_re = re.compile(r"(?<=^PLAYERS )[{}]+$".format(id_charset))
name_re = re.compile(r"(?<=^NAME )[a-zA-Z0-9-_.]+$")
create_re = re.compile(r"(?<=^CREATE ).+$")


class MatchError(Exception):
    pass


class NetworkGame(object):
    id_length = 1
    def __init__(self, num_players):
        self.id, self.uri = NetworkGame.id_gen()
        self.serializer = SerializationInterface()
        
        self.game = None
        self.players = []
        self.max_players = num_players
        matches[self.id] = self
        
        self.turn = 0
        self.actions = [None] * num_players
    
    def player_id(self, player):
        return self.players.index(player)
    
    @classmethod
    def id_gen(cls):
        while True:
            i = ''.join(random.choice(id_charset)
                        for _ in range(cls.id_length))
            if i in matches:
                cls.id_length += 1
            else:
                break
        
        return i, 'rg-match://{host}:{port}/{id}'.format(
            host=host, port=port, id=i)

    
    def add_player(self, player):
        if len(self.players) < self.max_players:
            self.players.append(player)
            player.match = self
            player.state = "JOINED"
            player.write(self.uri)
        else:
            raise MatchError("Match is full")
    
    def add_actions(self, player, actions_str):
        player_robots = {l:r for l,r in self.game._state.robots.items()
                         if r['player_id'] == player._player_id}
        def sanitize(actions):
            a = {}
            # robots for which actions were not given
            for loc in player_robots:
                try:
                    a[loc] = actions[loc]
                except KeyError:
                    a[loc] = ('guard')
                    print "missing action for", loc, "player=", id(player)
            return a
        
        player_id = self.player_id(player)
        actions = self.serializer.deserialize_actions(actions_str)
        print "actions=", repr(actions)
        actions = sanitize(actions)
        print "player_robots=", repr(player_robots)
        print "sanitizedactions=", repr(actions)
        self.actions[player_id] = actions
        # once all actions are processed
        
        if all(p_actions is not None for p_actions in self.actions):
            print "runturn"
            self.game.run_turn()
            
            if self.game._state.turn > settings.max_turns:
                for player in self.players:
                    player.state = "ENDED"
                    
            for player_id, player in enumerate(self.players):
                self.actions[player_id] = None
                self.send_gamestate(player)
                
            if self.game._state.turn > settings.max_turns:
                del matches[self.id]
                for player in self.players:
                    player.match = None
                    player.transport.loseConnection()
                print "Game {} ended".format(self.id)
        
    def send_gamestate(self, player):
        gamestate_str = self.serializer.serialize(self.game._state)
        player.write(gamestate_str)
    
    def start(self, player):       
        if len(self.players) == self.max_players:
            player.state = "STARTED"
            sett_str = self.serializer.serialize(settings, self.player_id(player))
            player.write(sett_str)
            
            if self.game is None:
                #print settings
                self.game = Game(self.players, record_actions=False,
                                record_history=False, symmetric=True)
                self.actions = [{}] * self.max_players
                self.game.run_turn() #spawn bots
                self.actions = [None] * self.max_players
            
        else:
            raise MatchError("Match is not full")
    
    def abort(self):
        del matches[self.id]
        for player in self.players:
            player.match = None
            player.state = "DISCONNECTED"
            player.write("Player disconnected from match.")
            player.transport.loseConnection()
        print "Game {} aborted".format(self.id)

class Player(NetstringReceiver, TimeoutMixin):
    """
    states = CONNECTED | JOINED | STARTED | TURN | DISCONNECTED
    """
    def __init__(self, factory):
        self.match = None
        self._player_id = None
        
        self.state = "CONNECTED"
        self.name = "Unnamed Player"
        self.factory = factory
        self.MAX_LENGTH = 128  # seconds
        self.setTimeout(timeout_in_s)

    def connectionMade(self):
        self.factory.numProtocols = self.factory.numProtocols + 1
        peer = self.transport.getPeer()
        self.peer = "{}:{}".format(peer.host, peer.port)
        self.write(
            "Welcome! There are currently %d open connections." %
            (self.factory.numProtocols,))
        if self.factory.numProtocols > 8:
            self.state = "DISCONNECTED"
            self.write("Sorry, too many players connected!")
            self.transport.loseConnection()

    def connectionLost(self, reason):
        print "dc", id(self)
        self.factory.numProtocols = self.factory.numProtocols - 1
        if self.match:  # should only exist if JOINED or STARTED
            self.match.abort()

    def write(self, data):
        s = "{} {}".format(self.state, data)
        print ">> {} {}: {}".format(self.peer, id(self), repr(s))
        self.sendString(s)

    def stringReceived(self, data):
        print "<< {} {}: {}".format(self.peer, id(self), repr(data))
        if self.state == "CONNECTED":
            
            # NAME <name>
            if re.search(name_re, data):
                self.name = re.search(name_re, data).group(0)
                self.write("Hello, {}".format(self.name))
            
            # JOIN <match_id>
            elif re.search(join_re, data):
                match_id = re.search(join_re, data).group(0)
                self.join_match(match_id)
            
            # CREATE <match_options>
            elif re.search(create_re, data):
                options = {}
                for option_string in shlex.split(re.search(create_re, data).group(0)):
                    try:
                        var, val = option_string.split('=')
                        options[var] = val
                    except ValueError:
                        self.write("Malformed option syntax")
                        return
                self.create_match(options)
            
            # LIST
            elif data == "LIST":
                self.print_matches()
            
            # PLAYERS <match_id>
            elif re.search(players_re, data):
                match_id = re.search(players_re, data).group(0)
                self.print_players(match_id)
                    
            else:
                self.write("Invalid command")

        elif self.state == "JOINED": 
            if data == "START":
                try:
                    self.match.start(self)
                except MatchError:
                    self.print_players(self.match.id)
        elif self.state == "STARTED":
            if data == "TURN":
                self.state = "TURN"
                self.match.send_gamestate(self)
            else:
                self.loseConnection()
        elif self.state == "TURN":
            s = "TURN "
            if data.startswith(s):
                actions_str = data[len(s):]
                self.match.add_actions(self, actions_str)
            else:
                self.loseConnection()
        else:
            raise Exception("Unknown state")
    
    def print_matches(self):
        if matches:
            self.write(", ".join("{} ({}/{}): ".format(
                match.id, len(match.players), match.max_players)
                for match in matches.values()))
        else:
            self.write("No matches created.")
    
    def print_players(self, match_id):
        try:
            match = matches[match_id]
            list_str = "{} ({}/{}) ".format(
                match.uri, len(match.players), match.max_players)
            
            self.write(list_str + ", ".join("{} {}".format(
                player.name, id(player)) for player in match.players))
        except KeyError:
            self.write("Match does not exist!")
    
    def create_match(self, options):
        try:
            num_players = int(options['num_players'])
            if not (1 <= num_players <= 2):
                raise ValueError
        except KeyError:
            self.write("Missing num_players option")
        except ValueError:
            self.write("Invalid num_players value")
        else:
            match = NetworkGame(num_players)
            self.join_match(match.id)

    def join_match(self, match_id):
        try:
            matches[match_id].add_player(self)
        except KeyError:
            self.write("Match does not exist!")
        except MatchError:
            self.write("Match is full!")
            
    def get_responses(self, state, seed):
        return self.match.actions[self._player_id], {}
    
    def set_player_id(self, pid):
        self._player_id = pid
        
        
class PlayerFactory(Factory):
    numProtocols = 0
    def buildProtocol(self, addr):
        return Player(self)
    

#import google.protobuf.socketrpc.server as server
#server = server.SocketRpcServer(8007)
#server.run()
    
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run a robotgame match/turn resolution service")
    parser.add_argument('host', type=str, help="Host to set for URIs")
    parser.add_argument('port', type=int, help="Port to run under")

    args = parser.parse_args()
    host = args.host
    port = args.port

    reactor.suggestThreadPoolSize(4)
    endpoint = TCP4ServerEndpoint(reactor, port)
    endpoint.listen(PlayerFactory())
    reactor.run()
