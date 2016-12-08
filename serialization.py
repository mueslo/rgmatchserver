import ast
from rgkit.run import Options
map_data = ast.literal_eval(open(Options().map_filepath).read())

from rgkit.game import settings
from rgkit.settings import Settings
from rgkit.gamestate import GameState
import match_pb2

action_type = {
    'ATTACK': match_pb2.ATTACK,
    'SUICIDE': match_pb2.SUICIDE,
    'MOVE': match_pb2.MOVE,
    'GUARD': match_pb2.GUARD
    }

# add inverse mapping
action_type.update(
    zip(action_type.values(), action_type.keys()))

class SerializationInterface(object):
    def serialize_settings(self, *args, **kwargs):
        raise NotImplementedError

    def deserialize_settings(self, match_str):
        raise NotImplementedError

    def serialize_gamestate(self, gamestate, player_id):
        raise NotImplementedError

    def deserialize_gamestate(self, gamestate_str):
        raise NotImplementedError

    def serialize_actions(self, actions, turn):
        raise NotImplementedError
    
    def deserialize_actions(self, actions_str):
        raise NotImplementedError
    
    def serialize(self, obj, *args, **kwargs):
        if type(obj) == GameState:
            return self.serialize_gamestate(obj, *args, **kwargs)
        elif type(obj) == Settings:
            return self.serialize_settings(obj, *args, **kwargs)
        raise NotImplementedError

# protobuf2 interface
class PB2Interface(SerializationInterface):
    def serialize_settings(self, settings, pid):
        settings.init_map(map_data)
        
        sett = match_pb2.Settings(player_id=pid)
        sett.map.board_size = 19
        
        for x,y in map_data['obstacle']:
            sett.map.obstacle_tiles.add(x=x, y=y)
        for x,y in map_data['spawn']:
            sett.map.spawn_tiles.add(x=x, y=y)

        sett.spawn_period = settings.spawn_every
        sett.num_players = settings.player_count
        sett.spawn_amount = settings.spawn_per_player
        sett.turns = settings.max_turns
        
        return sett.SerializeToString()

    def deserialize_settings(self, sett_str):
        sett = match_pb2.Settings()
        sett.ParseFromString(sett_str)
        
        # disabled until per-match settings are easier
        # (server only supports global default settings currently)
        
        settings.spawn_every = sett.spawn_period
        settings.player_count = sett.num_players
        settings.spawn_per_player = sett.spawn_amount
        settings.max_turns = sett.turns
        
        settings.board_size = sett.map.board_size
        map_data = {'obstacle': [(tile.x, tile.y) for tile in sett.map.obstacle_tiles],
                    'spawn': [(tile.x, tile.y) for tile in sett.map.spawn_tiles]}
        settings.init_map(map_data)
        return settings, sett.player_id

    def serialize_gamestate(self, gamestate, player_id=None):
        # knowing player_id would be neede for hiding ids
        # but the point of hiding bot ids is beyond me anyway, so I'm not bothering
        state = match_pb2.State(turn=gamestate.turn)
        for loc, bot in gamestate.robots.items():
            location = match_pb2.Settings.Map.Coordinate(
                x=bot['location'][0], y=bot['location'][1])
            state.bots.add(
                id=bot['robot_id'], location=location, hp=bot['hp'],
                player_id=bot['player_id'])
        
        #print state
        print "turn:", state.turn
        return state.SerializeToString()

    def deserialize_gamestate(self, gamestate_str):
        state = match_pb2.State()
        state.ParseFromString(gamestate_str)
        gs = GameState(turn=state.turn)
        for bot in state.bots:
            gs.add_robot((bot.location.x, bot.location.y),
                         bot.player_id, bot.hp, bot.id)
            
        return gs

    def serialize_actions(self, actions, bots, turn):
        actions_pb = match_pb2.Actions(turn=turn)
        for loc, act in actions.items():
            action = actions_pb.actions.add(
                bot_id=bots[loc]['robot_id'],
                location=match_pb2.Settings.Map.Coordinate(x=loc[0], y=loc[1]),
                type=action_type[act[0]])
            try:
                targ_loc = act[1]
            except IndexError:
                pass
            else:
                action.target = match_ob2.Settings.Map.Coordinate(
                    x=targ_loc[0], y=targ_loc[1])
                
        return actions_pb.SerializeToString()
    
    def deserialize_actions(self, actions_str):
        actions = match_pb2.Actions()
        actions.ParseFromString(actions_str)
        
        actions = {(action.location.x, action.location.y):
                       (action_type[action.type], (action.target.x, action.target.y)) for action in actions.actions}
        
        return actions

# json interface
class JSONInterface(SerializationInterface):
    def deserialize_gamestate(self, gamestate_str):
        return GameState.create_from_json(gamestate_str)
            
    def deserialize_actions(self, actions_str):
        return GameState.create_actions_from_json(actions)
