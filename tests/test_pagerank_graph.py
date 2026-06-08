"""Unit test for the PageRank graph behavior (no database required).

Mirrors how `app.ranking.pagerank.compute_pagerank` builds and scores the graph,
verifying the property we rely on: a page that everyone links to outranks the rest.
"""

from __future__ import annotations

import networkx as nx

from app.config import settings


def test_hub_dominates_and_scores_sum_to_one():
    # Pages 1, 2, 3 all link to the hub (4); the hub links back to 1.
    edges = [(1, 4), (2, 4), (3, 4), (4, 1)]
    g = nx.DiGraph()
    g.add_nodes_from([1, 2, 3, 4])
    g.add_edges_from(edges)

    scores = nx.pagerank(g, alpha=settings.pagerank_damping)

    assert abs(sum(scores.values()) - 1.0) < 1e-6
    # The hub receives the most in-links and must rank highest.
    assert max(scores, key=scores.get) == 4
    assert scores[4] > scores[1] > scores[2]
