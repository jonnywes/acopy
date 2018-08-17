# -*- coding: utf-8 -*-
import sys
import functools
import textwrap
import collections

from . import utils


@functools.total_ordering
class Solution:
    def __init__(self, graph, start, ant=None):
        self.graph = graph
        self.start = start
        self.ant = ant
        self.current = start
        self.weight = 0
        self.path = []
        self.nodes = [start]
        self.visited = set(self.nodes)

    def __iter__(self):
        return iter(self.path)

    def __eq__(self, other):
        return self.weight == other.weight

    def __lt__(self, other):
        return self.weight < other.weight

    def __contains__(self, node):
        return node in self.visited or node == self.current

    def __repr__(self):
        id_ = [str(n) for n in self.get_id()]
        size = max([len(n) for n in id_])
        easy_id = ' '.join(n.rjust(size) for n in id_)
        return '{} ({}, {})'.format(easy_id, self.weight, self.ant)

    def __hash__(self):
        return hash(self.get_id())

    def get_id(self):
        first = min(self.nodes)
        index = self.nodes.index(first)
        return tuple(self.nodes[index:] + self.nodes[:index])

    def add_node(self, node):
        self.nodes.append(node)
        self.visited.add(node)
        self._add_node(node)

    def close(self):
        self._add_node(self.start)

    def _add_node(self, node):
        edge = self.current, node
        data = self.graph.edges[edge]
        self.path.append(edge)
        self.weight += data['weight']
        self.current = node

    def trace(self, q, rho=0):
        amount = q / self.weight
        for edge in self.path:
            self.graph.edges[edge]['pheromone'] += amount
            self.graph.edges[edge]['pheromone'] *= 1 - rho
            if not self.graph.edges[edge]['pheromone']:
                self.graph.edges[edge]['pheromone'] = sys.float_info.min


class State:
    def __init__(self, graph, ants, limit, gen_size):
        self.graph = graph
        self.ants = ants
        self.limit = limit
        self.gen_size = gen_size
        self.solutions = None
        self.record = None
        self.previous_record = None
        self.is_new_record = False
        self.best = None

    @property
    def best(self):
        return self._best

    @best.setter
    def best(self, best):
        self.is_new_record = self.record is None or best < self.record
        if self.is_new_record:
            self.old_record = self.record
            self.record = best
        self._best = best


class Solver:
    """ACO solver.

    Solvers control the parameters related to phomone deposit and evaporation.
    If top is not specified, it defaults to the number of ants used to solve a
    graph.

    :param float rho: percentage of pheromone that evaporates each iteration
    :param float q: amount of pheromone each ant can deposit
    :param int top: number of ants that deposit pheromone
    :param list plugins: zero or more solver plugins
    """

    def __init__(self, rho=.03, q=1, top=None, plugins=None):
        self.rho = rho
        self.q = q
        self.top = top
        self.plugins = collections.OrderedDict()
        if plugins:
            self.add_plugins(*plugins)

    def __repr__(self):
        return (f'{self.__class__.__name__}(rho={self.rho}, q={self.q}, '
                f'top={self.top})')

    def __str__(self):
        plugin_reprs = '\n'.join([repr(p) for p in self.get_plugins()])
        plugins = textwrap.indent(plugin_reprs, prefix='  ')
        return f'{repr(self)}\n{plugins}'

    def solve(self, *args, **kwargs):
        """Find and return the best solution.

        Accepts exactly the same paramters as the :func:`~optimize` method.

        :return: best solution found
        :rtype: :class:`~Solution`
        """
        best = None
        for solution in self.optimize(*args, **kwargs):
            best = solution
        return best

    def optimize(self, graph, colony, gen_size=None, limit=None):
        """Find and return increasingly better solutions.

        :param graph: graph to solve
        :type graph: :class:`networkx.Graph`
        :param colony: colony from which to source each :class:`~acopy.ant.Ant`
        :type colony: :class:`~acopy.ant.Colony`
        :param int gen_size: number of :class:`~acopy.ant.Ant` s to use
                             (default is one per graph node)
        :param int limit: maximum number of iterations to perform (default is
                          unlimited so it will run forever)
        :return: better solutions as they are found
        :rtype: iter
        """
        # initialize the colony of ants and the graph
        gen_size = gen_size or len(graph.nodes)
        ants = colony.get_ants(gen_size)
        for u, v in graph.edges:
            graph.edges[u, v].setdefault('pheromone', 0)

        state = State(graph=graph, ants=ants, limit=limit, gen_size=gen_size)

        # call start hook for all plugins
        self._call_plugins('start', state=state)

        # find solutions and update the graph pheromone accordingly
        for __ in utils.looper(limit):
            solutions = self.find_solutions(state.graph, state.ants)

            # we want to ensure the ants are sorted with the solutions, but
            # since ants aren't directly comparable, so we interject a list of
            # unique numbers that satifies any two solutions that are equal
            data = list(zip(solutions, range(len(state.ants)), state.ants))
            data.sort()
            solutions, __, ants = zip(*data)

            state.solutions = solutions
            state.ants = ants
            self.global_update(state)

            # yield increasingly better solutions
            state.best = state.solutions[0]
            if state.is_new_record:
                yield state.record

            # call iteration hook for all plugins
            if self._call_plugins('iteration', state=state):
                break

        # call finish hook for all plugins
        self._call_plugins('finish', state=state)

    def find_solutions(self, graph, ants):
        return [ant.tour(graph) for ant in ants]

    def global_update(self, state):
        for edge in state.graph.edges:
            amount = 0
            if self.top:
                solutions = state.solutions[:self.top]
            else:
                solutions = state.solutions
            for solution in solutions:
                if edge in solution.path:
                    amount += self.q / solution.weight
            p = state.graph.edges[edge]['pheromone']
            state.graph.edges[edge]['pheromone'] = (1 - self.rho) * p + amount

    def add_plugin(self, plugin):
        self.add_plugins(plugin)

    def add_plugins(self, *plugins):
        for plugin in plugins:
            plugin.initialize(self)
            self.plugins[plugin.name] = plugin

    def get_plugins(self):
        return self.plugins.values()

    def _call_plugins(self, hook, **kwargs):
        should_stop = False
        for plugin in self.get_plugins():
            try:
                plugin(hook, **kwargs)
            except StopIteration:
                should_stop = True
        return should_stop


class SolverPlugin:
    name = 'plugin'

    def __init__(self, **kwargs):
        self._params = kwargs

    def __repr__(self):
        params = ', '.join(f'{k}={v}'for k, v in self._params.items())
        return f'{self.name}[{params}]'

    def __call__(self, hook, **kwargs):
        return getattr(self, f'on_{hook}')(**kwargs)

    def initialize(self, solver):
        self.solver = solver

    def on_start(self, state):
        pass

    def on_iteration(self, state):
        pass

    def on_finish(self, state):
        pass