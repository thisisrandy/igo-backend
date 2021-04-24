from game import ActionType, Color, Game
from messages import (
    IncomingMessage,
    IncomingMessageType,
    OutgoingMessage,
    OutgoingMessageType,
)
import unittest
from unittest.mock import MagicMock, patch
from tornado.websocket import WebSocketHandler
from constants import TYPE, VS, COLOR, KOMI, KEY, ACTION_TYPE
import json


@patch.object(WebSocketHandler, "__init__", lambda self: None)
class IncomingMessageTestCase(unittest.TestCase):
    def test_create_message(self):
        # test required keys (incorrect)
        with self.assertRaises(AssertionError):
            IncomingMessage(
                json.dumps({TYPE: IncomingMessageType.new_game.name}),
                WebSocketHandler(),
            )
        with self.assertRaises(AssertionError):
            IncomingMessage(
                json.dumps({TYPE: IncomingMessageType.join_game.name}),
                WebSocketHandler(),
            )
        with self.assertRaises(AssertionError):
            IncomingMessage(
                json.dumps({TYPE: IncomingMessageType.game_action.name}),
                WebSocketHandler(),
            )

        # test required keys (correct)
        try:
            IncomingMessage(
                json.dumps(
                    {
                        TYPE: IncomingMessageType.new_game.name,
                        VS: "human",
                        COLOR: Color.white.name,
                        KOMI: 6.5,
                    }
                ),
                WebSocketHandler(),
            )
        except AssertionError as e:
            self.fail(
                f"Correctly specified IncomingMessage still failed required key assertion: {e}"
            )
        try:
            IncomingMessage(
                json.dumps(
                    {TYPE: IncomingMessageType.join_game.name, KEY: "0123456789"}
                ),
                WebSocketHandler(),
            )
        except AssertionError:
            self.fail(
                f"Correctly specified IncomingMessage still failed required key assertion: {e}"
            )
        try:
            IncomingMessage(
                json.dumps(
                    {
                        TYPE: IncomingMessageType.game_action.name,
                        KEY: "0123456789",
                        ACTION_TYPE: ActionType.place_stone.name,
                        COLOR: Color.white.name,
                    }
                ),
                WebSocketHandler(),
            )
        except AssertionError:
            self.fail(
                f"Correctly specified IncomingMessage still failed required key assertion: {e}"
            )


@patch.object(WebSocketHandler, "__init__", lambda self: None)
class OutgoingMessageTestCase(unittest.TestCase):
    def test_jsonify(self):
        msg = OutgoingMessage(
            OutgoingMessageType.game_status, Game(1), WebSocketHandler()
        )
        self.assertEqual(
            msg._jsonify(),
            json.dumps(
                {
                    "message_type": OutgoingMessageType.game_status.name,
                    "data": Game(1).jsonifyable(),
                }
            ),
        )

    @patch("tornado.websocket.WebSocketHandler.write_message")
    def test_send(self, mock_write_message: MagicMock):
        msg = OutgoingMessage(
            OutgoingMessageType.game_status, Game(1), WebSocketHandler()
        )
        msg.send()
        mock_write_message.assert_called()