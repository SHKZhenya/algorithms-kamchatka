import unittest

import numpy as np

from simulate import AREA, STEPS, build_event, candidate_routes, decode, run, step


class SimulationTests(unittest.TestCase):
    def test_nonnegative_mass_balance(self):
        initial, snow, *_ = build_event(101, 1, 0)
        state = initial
        rng = np.random.default_rng(71)
        for precipitation in snow:
            previous = np.sum(AREA * (state[0] + state[1]) + state[2])
            state, net = step(state, precipitation, "truth", rng.random(len(AREA)) < 0.2)
            total = np.sum(AREA * (state[0] + state[1]) + state[2])
            self.assertTrue(np.all(state >= 0))
            self.assertAlmostEqual(total - previous, net, places=8)
        zero = np.zeros_like(state)
        result, net = step(zero, 0, "truth", np.ones(len(AREA), dtype=bool))
        np.testing.assert_array_equal(result, zero)
        self.assertEqual(net, 0)

    def test_depot_return_and_distinct_service(self):
        routes = candidate_routes(np.array([1, 11, 0]), 1, STEPS - 1, 23)
        edges, service, _ = decode(routes[0])
        np.testing.assert_array_equal(edges[:, :2], np.tile([3, 22], (len(edges), 1)))
        self.assertTrue(np.all(routes[0, :, 2] == -1))
        _, same_edge, costs = decode(np.array([2, 2, 0]))
        self.assertEqual(same_edge.sum(), 1)
        self.assertEqual(costs, 2.55)

    def test_repeatable_paired_run(self):
        event = build_event(203, 0.65, 0.35)
        _, first = run("B2", 203, event)
        _, second = run("B2", 203, event)
        np.testing.assert_array_equal(first, second)


if __name__ == "__main__":
    unittest.main()
