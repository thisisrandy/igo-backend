from datetime import datetime
from game import Action, ActionType, Board, Color, Game, Point
import unittest


class PointTestCase(unittest.TestCase):
    def test_json(self):
        self.assertEqual("", Point().jsonifyable())
        self.assertEqual("w", Point(Color.white).jsonifyable())
        self.assertEqual("b", Point(Color.black).jsonifyable())
        self.assertEqual("wd", Point(Color.white, True).jsonifyable())
        self.assertEqual("bd", Point(Color.black, True).jsonifyable())


class BoardTestCase(unittest.TestCase):
    def test_json(self):
        board = Board(3)
        self.assertEqual(
            board.jsonifyable(), [["", "", ""], ["", "", ""], ["", "", ""]]
        )
        board[0][1].color = Color.black
        self.assertEqual(
            board.jsonifyable(), [["", "b", ""], ["", "", ""], ["", "", ""]]
        )

    def test_eq(self):
        b1, b2, b3 = Board(3), Board(3), Board(5)
        self.assertEqual(b1, b2)
        self.assertNotEqual(b2, b3)
        b1[0][0].color = Color.black
        b2[0][0].color = Color.black
        self.assertEqual(b1, b2)
        b1[0][1].color = Color.white
        self.assertNotEqual(b1, b2)


class GameTestCase(unittest.TestCase):
    def test_placement(self):
        g = Game(3)
        b = Board(3)
        success, _ = g.take_action(
            Action(
                ActionType.placement, Color.white, (0, 0), datetime.now().timestamp()
            )
        )
        self.assertTrue(success)
        b[0][0].color = Color.white
        self.assertEqual(g.board, b)

    def test_turn(self):
        g = Game(3)
        success, msg = g.take_action(
            Action(
                ActionType.placement, Color.black, (0, 0), datetime.now().timestamp()
            )
        )
        self.assertFalse(success)
        self.assertEqual(msg, "It isn't black's turn")
        g.take_action(
            Action(
                ActionType.placement, Color.white, (0, 0), datetime.now().timestamp()
            )
        )
        success, msg = g.take_action(
            Action(
                ActionType.placement, Color.white, (0, 1), datetime.now().timestamp()
            )
        )
        self.assertFalse(success)
        self.assertEqual(msg, "It isn't white's turn")

    def test_occupied(self):
        g = Game(3)
        g.take_action(
            Action(
                ActionType.placement, Color.white, (0, 0), datetime.now().timestamp()
            )
        )
        success, msg = g.take_action(
            Action(
                ActionType.placement, Color.black, (0, 0), datetime.now().timestamp()
            )
        )
        self.assertFalse(success)
        self.assertEqual(msg, "Point (0, 0) is occupied")

    def test_suicide(self):
        g = Game(3)
        ts = datetime.now().timestamp()
        actions = [
            Action(ActionType.placement, Color.white, (1, 0), ts),
            Action(ActionType.placement, Color.black, (0, 0), ts),
            Action(ActionType.placement, Color.white, (1, 1), ts),
            Action(ActionType.placement, Color.black, (0, 1), ts),
            Action(ActionType.placement, Color.white, (1, 2), ts),
            Action(ActionType.placement, Color.black, (0, 2), ts),
        ]
        for a in actions:
            success, msg = g.take_action(a)
        self.assertFalse(success)
        self.assertEqual(msg, "Playing at (0, 2) is suicide")

    def test_ko(self):
        g = Game(4)
        ts = datetime.now().timestamp()
        actions = [
            Action(ActionType.placement, Color.white, (1, 0), ts),
            Action(ActionType.placement, Color.black, (2, 0), ts),
            Action(ActionType.placement, Color.white, (0, 1), ts),
            Action(ActionType.placement, Color.black, (3, 1), ts),
            Action(ActionType.placement, Color.white, (1, 2), ts),
            Action(ActionType.placement, Color.black, (2, 2), ts),
            Action(ActionType.placement, Color.white, (2, 1), ts),
            Action(ActionType.placement, Color.black, (1, 1), ts),
            Action(ActionType.placement, Color.white, (2, 1), ts),
        ]
        for a in actions:
            success, msg = g.take_action(a)
        self.assertFalse(success)
        self.assertEqual(msg, "Playing at (2, 1) violates the simple ko rule")

    def test_capture(self):
        g = Game(5)
        ts = datetime.now().timestamp()
        actions = [
            Action(ActionType.placement, Color.white, (0, 0), ts),
            Action(ActionType.placement, Color.black, (1, 0), ts),
            Action(ActionType.placement, Color.white, (0, 1), ts),
            Action(ActionType.placement, Color.black, (1, 1), ts),
            Action(ActionType.placement, Color.white, (0, 2), ts),
            Action(ActionType.placement, Color.black, (2, 2), ts),
            Action(ActionType.placement, Color.white, (1, 2), ts),
            Action(ActionType.placement, Color.black, (1, 3), ts),
            Action(ActionType.placement, Color.white, (0, 3), ts),
            Action(ActionType.placement, Color.black, (0, 4), ts),
        ]
        for a in actions:
            success, msg = g.take_action(a)
        self.assertEqual(len(g.action_stack), len(actions))
        self.assertEqual(g.prisoners[Color.white], 0)
        self.assertEqual(g.prisoners[Color.black], 5)