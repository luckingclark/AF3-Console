# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2004-2026, NetworkX Developers
# Aric Hagberg, Dan Schult, and Pieter Swart. All rights reserved.
#
# Adapted for AF3 Console; refactored into this standalone module on 2026-09-13.
# The historical upstream revision of the original port was not recorded.
# Current reference reviewed at NetworkX commit:
# 4e74880b0da01977da79915167c64e5c2af38b47
# Sources: networkx/utils/mapped_queue.py and
# networkx/algorithms/community/modularity_max.py.
# Original algorithm bodies are unchanged by this module extraction.
# Full copyright, conditions, and disclaimer:
# LICENSES/BSD-3-Clause-NetworkX.txt.

"""Dependency-free NetworkX community/priority-queue adaptation (BSD-3-Clause)."""
import heapq


class _HeapElement:
    """按 priority 排序(平手按 element);相等/哈希按 element。"""

    __slots__ = ["priority", "element", "_hash"]

    def __init__(self, priority, element):
        self.priority = priority
        self.element = element
        self._hash = hash(element)

    def __lt__(self, other):
        try:
            other_priority = other.priority
        except AttributeError:
            return self.priority < other
        if self.priority == other_priority:
            return self.element < other.element
        return self.priority < other_priority

    def __eq__(self, other):
        try:
            return self.element == other.element
        except AttributeError:
            return self.element == other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return self._hash

    def __getitem__(self, indx):
        return self.priority if indx == 0 else self.element[indx - 1]

    def __iter__(self):
        yield self.priority
        try:
            for x in self.element:
                yield x
        except TypeError:
            yield self.element


class _MappedQueue:
    """支持删除/改优先级的最小堆(networkx MappedQueue 移植)。"""

    def __init__(self, data=None):
        if data is None:
            self.heap = []
        elif isinstance(data, dict):
            self.heap = [_HeapElement(v, k) for k, v in data.items()]
        else:
            self.heap = list(data)
        self.position = {}
        self._heapify()

    def _heapify(self):
        heapq.heapify(self.heap)
        self.position = {elt: pos for pos, elt in enumerate(self.heap)}
        if len(self.heap) != len(self.position):
            raise AssertionError("Heap contains duplicate elements")

    def __len__(self):
        return len(self.heap)

    def push(self, elt, priority=None):
        if priority is not None:
            elt = _HeapElement(priority, elt)
        if elt in self.position:
            return False
        pos = len(self.heap)
        self.heap.append(elt)
        self.position[elt] = pos
        self._siftdown(0, pos)
        return True

    def pop(self):
        elt = self.heap[0]
        del self.position[elt]
        if len(self.heap) == 1:
            self.heap.pop()
            return elt
        last = self.heap.pop()
        self.heap[0] = last
        self.position[last] = 0
        self._siftup(0)
        return elt

    def update(self, elt, new, priority=None):
        if priority is not None:
            new = _HeapElement(priority, new)
        pos = self.position[elt]
        self.heap[pos] = new
        del self.position[elt]
        self.position[new] = pos
        self._siftup(pos)

    def remove(self, elt):
        pos = self.position[elt]
        del self.position[elt]
        if pos == len(self.heap) - 1:
            self.heap.pop()
            return
        last = self.heap.pop()
        self.heap[pos] = last
        self.position[last] = pos
        self._siftup(pos)

    def _siftup(self, pos):
        heap, position = self.heap, self.position
        end_pos = len(heap)
        newitem = heap[pos]
        child_pos = (pos << 1) + 1
        while child_pos < end_pos:
            child = heap[child_pos]
            right_pos = child_pos + 1
            if right_pos < end_pos:
                right = heap[right_pos]
                if not child < right:
                    child = right
                    child_pos = right_pos
            heap[pos] = child
            position[child] = pos
            pos = child_pos
            child_pos = (pos << 1) + 1
        while pos > 0:
            parent_pos = (pos - 1) >> 1
            parent = heap[parent_pos]
            if not newitem < parent:
                break
            heap[pos] = parent
            position[parent] = pos
            pos = parent_pos
        heap[pos] = newitem
        position[newitem] = pos

    def _siftdown(self, start_pos, pos):
        heap, position = self.heap, self.position
        newitem = heap[pos]
        while pos > start_pos:
            parent_pos = (pos - 1) >> 1
            parent = heap[parent_pos]
            if not newitem < parent:
                break
            heap[pos] = parent
            position[parent] = pos
            pos = parent_pos
        heap[pos] = newitem
        position[newitem] = pos


def _greedy_modularity_gen(nodes, adj, resolution=1.0):
    """networkx _greedy_modularity_communities_generator 移植(无向带权)。
    adj: dict-of-dict, adj[u][v] = weight(双向,u != v)。"""
    m = 0.0
    degree = {}
    for u in nodes:
        d = 0.0
        for v, w in adj.get(u, {}).items():
            d += w
        degree[u] = d
        m += d
    m *= 0.5
    if m <= 0:
        yield {u: frozenset([u]) for u in nodes}.values()
        return
    q0 = 1.0 / m
    a = {u: degree[u] * q0 * 0.5 for u in nodes}

    dq_dict = {}
    for u in nodes:
        row = {}
        for v, w in adj.get(u, {}).items():
            if v == u:
                continue
            row[v] = row.get(v, 0.0) + w
        dq_dict[u] = row
    for u, nbrdict in dq_dict.items():
        au = a[u]
        for v, wt in nbrdict.items():
            nbrdict[v] = q0 * wt - resolution * (au * a[v] + au * a[v])

    dq_heap = {u: _MappedQueue({(u, v): -dq for v, dq in dq_dict[u].items()})
               for u in nodes}
    H = _MappedQueue([dq_heap[u].heap[0] for u in nodes if len(dq_heap[u]) > 0])

    communities = {u: frozenset([u]) for u in nodes}
    yield communities.values()

    while len(H) > 1:
        try:
            negdq, u, v = H.pop()
        except IndexError:
            break
        dq = -negdq
        yield dq
        dq_heap[u].pop()
        if len(dq_heap[u]) > 0:
            H.push(dq_heap[u].heap[0])
        if dq_heap[v].heap[0] == (v, u):
            H.remove((v, u))
            dq_heap[v].remove((v, u))
            if len(dq_heap[v]) > 0:
                H.push(dq_heap[v].heap[0])
        else:
            dq_heap[v].remove((v, u))

        communities[v] = frozenset(communities[u] | communities[v])
        del communities[u]

        u_nbrs = set(dq_dict[u])
        v_nbrs = set(dq_dict[v])
        all_nbrs = (u_nbrs | v_nbrs) - {u, v}
        both_nbrs = u_nbrs & v_nbrs
        for w in all_nbrs:
            if w in both_nbrs:
                dq_vw = dq_dict[v][w] + dq_dict[u][w]
            elif w in v_nbrs:
                dq_vw = dq_dict[v][w] - resolution * (a[u] * a[w] + a[w] * a[u])
            else:
                dq_vw = dq_dict[u][w] - resolution * (a[v] * a[w] + a[w] * a[v])
            for row, col in ((v, w), (w, v)):
                dq_heap_row = dq_heap[row]
                dq_dict[row][col] = dq_vw
                if len(dq_heap_row) > 0:
                    d_oldmax = dq_heap_row.heap[0]
                else:
                    d_oldmax = None
                d = (row, col)
                d_negdq = -dq_vw
                if w in v_nbrs:
                    dq_heap_row.update(d, d, priority=d_negdq)
                else:
                    dq_heap_row.push(d, priority=d_negdq)
                if d_oldmax is None:
                    H.push(d, priority=d_negdq)
                else:
                    row_max = dq_heap_row.heap[0]
                    if d_oldmax != row_max or d_oldmax.priority != row_max.priority:
                        H.update(d_oldmax, row_max)

        for w in dq_dict[u]:
            del dq_dict[w][u]
            if w != v:
                for row, col in ((w, u), (u, w)):
                    dq_heap_row = dq_heap[row]
                    d_old = (row, col)
                    if len(dq_heap_row) > 0 and dq_heap_row.heap[0] == d_old:
                        dq_heap_row.remove(d_old)
                        H.remove(d_old)
                        if len(dq_heap_row) > 0:
                            H.push(dq_heap_row.heap[0])
                    else:
                        try:
                            dq_heap_row.remove(d_old)
                        except KeyError:
                            pass

        del dq_dict[u]
        dq_heap[u] = _MappedQueue()
        a[v] += a[u]
        a[u] = 0

        yield communities.values()


def _greedy_modularity_communities(nodes, adj, resolution=1.0):
    """networkx greedy_modularity_communities(cutoff=1, best_n=None)移植。"""
    nodes = list(nodes)
    gen = _greedy_modularity_gen(nodes, adj, resolution=resolution)
    communities = next(gen)
    best_n = len(nodes)
    while len(communities) > 1:
        try:
            dq = next(gen)
        except StopIteration:
            break
        if dq < 0 and len(communities) <= best_n:
            break
        communities = next(gen)
    return sorted(communities, key=len, reverse=True)
