from .types import DocumentMapping, DocumentSet
from collections import defaultdict
from matplotlib.colors import to_rgba, to_hex
import seaborn as sns
import json
import numpy as np
import networkx as nx
import pyvis
import textwrap


def calculate_layout(g, iterations=1000, gravity=1):
    try:
        from fa2 import ForceAtlas2
        model = ForceAtlas2(
                verbose=True,
                scalingRatio=1,
                gravity=gravity,
        )

        import networkx
        matrix = nx.to_scipy_sparse_matrix(g, dtype='f', format='lil', weight='weight')
        pos = model.forceatlas2(matrix, iterations=iterations)

        return dict(zip(g.nodes(), pos))

    except ImportError:
        return nx.drawing.layout.spring_layout(g)


def plot_network(g: nx.Graph, *, height='1000px', smooth_edges=None,
                 max_node_size=100, min_node_size=5, largest_component=True,
                 interactive=True, controls=False, scale=1, iterations=1000, gravity=1):
    """Plot a `networkx.Graph` generated by one of the `build_X_graph`
    functions in this module. Plotting is done using the `pyvis` library.


    :param height: Height of the plot.
    :param smooth_edges: Enables curved ('smooth') edges. Looks nice but is
                         heavy on performance.
    :param min_node_size: The radius of the smallest node.
    :param max_node_size: The radius of the largest node.
    :param largest_component: Only plot the largest connected component of the graph.
    """
    if isolates := list(nx.isolates(g)):
        g = g.copy()
        g.remove_nodes_from(isolates)

    if len(g.edges) == 0:
        print('no edges given')
        return

    directed = nx.is_directed(g)

    if largest_component:
        if directed:
            cc = nx.weakly_connected_components(g)
        else:
            cc = nx.connected_components(g)

        cc = sorted(cc, key=len, reverse=True)
        g = g.subgraph(cc[0])

    v = pyvis.network.Network(
            notebook=True,
            width='100%',
            height=height,
            directed=directed
    )

    sizes = [attr.get('weight') for (_, attr) in g.nodes.items()]

    if all(s is not None for s in sizes):
        sizes = np.array(sizes)
    elif directed:
        sizes = [g for (_, g) in g.in_degree]
    else:
        sizes = [g for (_, g) in g.degree]

    layout = calculate_layout(g, gravity=gravity, iterations=iterations)
    sizes = np.array(sizes, dtype=np.float32)
    ratio = (max_node_size - min_node_size) / np.amax(sizes)
    sizes = ratio * sizes + min_node_size

    for id, size in zip(g, sizes):
        attr = g.nodes[id]
        kwargs = dict(labelHighlightBold=True)

        kwargs['shape'] = 'dot'
        kwargs['title'] = attr['title']
        kwargs['label'] = textwrap.fill(attr['title'], width=20)
        kwargs['size'] = float(size)

        if layout is not None:
            pos = layout[id]
            kwargs['x'] = pos[0] * scale
            kwargs['y'] = pos[1] * scale

        color = attr.get('color')
        if color is not None:
            rgba = to_rgba(color)
            kwargs['color'] = to_hex(color)

        v.add_node(id, **kwargs)

    for src, dst in g.edges():
        weight = g[src][dst].get('weight')
        if weight is not None:
            width = weight
            label = str(weight)
        else:
            width = None
            label = ''

        v.add_edge(src, dst, width=width, title=label)

    if smooth_edges is None:
        smooth_edges = len(g.edges()) < 1000

    v.set_options(json.dumps({
        'configure': {
            'enabled': controls,
        },
        'nodes': {
            'font': {
                'size': 7,
            },
        },
        'edges': {
            'smooth': smooth_edges,
            'color': {
                'opacity': 0.25,
            }
        },
        'physics': {
            'enabled': interactive,
            'forceAtlas2Based': {
                'springLength': 100,
            },
            'solver': 'forceAtlas2Based',
        }
    }))

    return v.show('citation.html')


def build_base_network(docs, directed, colors=None, cmap=None,
        node_props=None):
    g = nx.DiGraph() if directed else nx.Graph()
    mapping = DocumentMapping()

    if node_props is None:
        node_props = docs.data.columns

    for i, doc in enumerate(docs):
        attr = dict()

        for prop in node_props:
            attr[prop] = docs.data[prop][i]

        g.add_node(i, title=doc.title, doc=doc, **attr)
        mapping.add(doc.id, i)


    if colors is not None and docs:
        # Column name
        if isinstance(colors, str):
            colors = docs[colors]
        else:
            colors = list(colors)

        assert len(colors) == len(docs)

        if all(isinstance(c, float) for c in colors):
            begin, end = min(colors), max(colors)
            cmap = sns.color_palette(cmap, as_cmap=True)
            colors = [cmap(float(c - begin) / (end - begin)) for c in colors]
        #if all(isinstance(c, int) for c in colors):
        else:
            groups = dict((c, i) for i, c in enumerate(sorted(set(colors))))
            cmap = sns.color_palette(cmap, n_colors=len(groups))
            colors = [cmap[groups[c]] for c in colors]


        for i, c in enumerate(colors):
            g.nodes[i]['color'] = c

    return g, mapping


def split_kwargs(*names, **kwargs):
    names = list(names) + ['colors', 'cmap']
    left, right = dict(), dict()

    for k, v in kwargs.items():
        if k in names:
            left[k] = v
        else:
            right[k] = v

    return left, right


def build_citation_network(docs: DocumentSet, **kwargs) -> nx.Graph:
    """Builds a citation network: a directed graph where each node
    corresponds to a document and each directed edge indicates that
    one document cites the other."""
    g, mapping = build_base_network(docs, True, **kwargs)

    for i, doc in enumerate(docs):
        for ref in doc.references or []:
            j = mapping.get(ref)

            if j is not None:
                g.add_edge(i, j)

    return g


def plot_citation_network(docs: DocumentSet, **kwargs):
    """Plot a citation network.

    This is a shorthand for `plot_network(build_citation_network(docs))`."""
    b, p = split_kwargs(**kwargs)
    return plot_network(build_citation_network(docs, **b), **p)


def build_cocitation_network(docs: DocumentSet, *, max_edges=None, **kwargs) -> nx.Graph:
    """Builds a co-citation network: a undirected graph where each node
    corresponds to a document and the edge weights stores the cocitation
    strengths (i.e., the number of times two documents have been cited
    together).

    :param max_edges: Select only the top edges. This is useful since
        cocitation networks are often dense and only the strongest edges
        are usually important.
    """
    max_edges = max_edges or len(docs) * 2

    g, mapping = build_base_network(docs, False, **kwargs)
    strength = defaultdict(int)

    for doc in docs:
        refs = []

        for ref in doc.references or []:
            j = mapping.get(ref)

            if j is not None:
                refs.append(j)

        for i in refs:
            for j in refs:
                if i < j:
                    strength[i, j] += 1

    edges = list(strength.items())

    if len(edges) > max_edges:
        edges.sort(key=lambda p: p[1], reverse=True)
        edges = edges[:max_edges]

    for (i, j), weight in edges:
        g.add_edge(i, j, weight=weight)

    return g


def plot_cocitation_network(docs: DocumentSet, *, max_edges=None,
                            node_size=10, **kwargs):
    """Plot a citation network.

    This is a shorthand for `plot_network(build_cocitation_network(docs))`."""
    b, p = split_kwargs(**kwargs)
    return plot_network(
            build_cocitation_network(docs, max_edges=max_edges, **b),
            # min_node_size=node_size,
            # max_node_size=node_size,
            **p
    )


def build_coupling_network(docs: DocumentSet, max_edges=1000, **kwargs) -> nx.Graph:
    """Builds a bibligraphic coupling network: an undirected graph where
    nodes indicate documents and edge weights store the bibliographic
    coupling strength. This strength measures how similar two documents
    view related work. It is measured as the number of shared references
    between two documents.

    :param max_edges: Select only the top edges. This is useful since these
        networks are often dense and only the strongest edges are usually
        important.
    """

    g, mapping = build_base_network(docs, False, **kwargs)
    n = len(g)
    doc_refs = []

    for doc in docs:
        refs = []

        for ref in doc.references or []:
            i = mapping.get(ref)

            if i is None:
                mapping.add(ref, n)
                n += 1
                i = n

            if i is not None:
                refs.append(i)

        doc_refs.append(set(refs))

    strength = defaultdict(int)

    for i, a in enumerate(doc_refs):
        for j, b in enumerate(doc_refs[:i]):
            common = a & b

            if common:
                strength[i, j] = len(common)

    edges = list(strength.items())

    if len(edges) > max_edges:
        edges.sort(key=lambda p: p[1], reverse=True)
        edges = edges[:max_edges]

    for (i, j), weight in edges:
        g.add_edge(i, j, weight=weight, score=weight)

    return g


def plot_coupling_network(docs: DocumentSet, *, max_edges=None, node_size=10,
                          **kwargs):
    """Plot a bibliographic coupling network.

    This is a shorthand for `plot_network(build_coupling_network(docs))`."""
    b, p = split_kwargs(**kwargs)
    return plot_network(
            build_coupling_network(docs, max_edges, **b),
            min_node_size=node_size,
            max_node_size=node_size,
            **p
    )


def build_coauthor_network(docs: DocumentSet, *, max_authors=None) -> nx.Graph:
    """Builds a co-author network: an undirected graph where nodes indicate
    authors and edge weight indicate the number of shared publications
    between two authors.

    :param max_authors: Select only the top X authors.
    """
    g = nx.DiGraph()
    count = defaultdict(int)

    for doc in docs:
        authors = []

        for author in doc.authors or []:
            name = author.name

            if name:
                count[name] += 1

    authors = list(count.keys())

    if max_authors is not None and len(authors) > max_authors:
        authors.sort(key=lambda name: count[name], reverse=True)
        authors = authors[:max_authors]

    mapping = dict()
    for i, author in enumerate(authors):
        g.add_node(i, title=author)
        mapping[author] = i

    edges = defaultdict(int)

    for doc in docs:
        authors = [a.name for a in doc.authors or [] if a.name]

        for i, left in enumerate(authors):
            for right in authors[:i]:
                if left in mapping and right in mapping:
                    edges[mapping[left], mapping[right]] += 1

    for (left, right), weight in edges.items():
        g.add_edge(left, right, weight=weight)

    return g


def plot_coauthor_network(docs: DocumentSet, *, max_authors=None, **kwargs):
    """Plot a co-author network.

    This is a shorthand for `plot_network(build_coauthor_network(docs))`."""
    b, p = split_kwargs(**kwargs)
    return plot_network(
            build_coauthor_network(docs, max_authors=max_authors, **b), **p
    )
