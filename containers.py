from typing import Dict, Optional
from game import Color
from dataclasses import dataclass
from messages import JsonifyableBase


@dataclass
class ResponseContainer(JsonifyableBase):
    """
    A base container for responses which implements jsonifyable

    Attributes:

        success: bool - indicator of the input action's success

        explanation: str - explanation of success
    """

    success: bool
    explanation: str

    def jsonifyable(self):
        return {"success": self.success, "explanation": self.explanation}


@dataclass
class GameResponseContainer(ResponseContainer):
    """
    A base container for the response to a game request which implements
    jsonifyable

    Attributes:

        success: bool - whether or not the request succeeded

        explanation: str - an explanatory string

        keys: Optional[Dict[Color, str]] = None - if success, a color to key
        mapping, and None otherwise

        your_color: Optional[Color] = None - if success, the color that the
        user is subscribed to, and None otherwise
    """

    keys: Optional[Dict[Color, str]] = None
    your_color: Optional[Color] = None

    def jsonifyable(self):
        return {
            **{
                "keys": {k.name: v for k, v in self.keys.items()}
                if self.keys
                else None,
                "your_color": self.your_color.name if self.your_color else None,
            },
            **super().jsonifyable(),
        }


@dataclass
class NewGameResponseContainer(GameResponseContainer):
    """
    A container for the response to a new game request which implements
    jsonifyable

    Attributes:

        success: bool - indicator of the input action's success

        explanation: str - explanation of success

        keys: Optional[Dict[Color, str]] = None - if success, a color to key
        mapping, and None otherwise

        your_color: Optional[Color] = None - if success, the color that the
        user is subscribed to, and None otherwise
    """

    pass


@dataclass
class JoinGameResponseContainer(GameResponseContainer):
    """
    A container for the response to a join game request which implements
    jsonifyable

    Attributes:

        success: bool - whether or not the join request succeeded

        explanation: str - an explanatory string

        keys: Optional[Dict[Color, str]] = None - if success, a color to key
        mapping, and None otherwise

        your_color: Optional[Color] = None - if success, the color that the
        user is subscribed to, and None otherwise
    """

    pass


@dataclass
class ActionResponseContainer(ResponseContainer):
    """
    A container for the response from Game.take_action which implements
    jsonifyable

    Attributes:

        success: bool - indicator of the input action's success

        explanation: str - explanation of success
    """

    pass


@dataclass
class OpponentConnectedContainer(JsonifyableBase):
    """
    Simple container for opponent's connectedness indicator which implements jsonifyable

    Attributes:

        opponent_connected: bool - indicator of the client's opponent's
        connectedness
    """

    opponent_connected: bool

    def jsonifyable(self) -> Dict:
        return {"opponentConnected": self.opponent_connected}