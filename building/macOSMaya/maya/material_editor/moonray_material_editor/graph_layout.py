"""Deterministic, size-aware layouts for shader DAGs, without Qt dependencies."""
from collections import defaultdict, deque


def _pack_column(nodes, desired, sizes, order, gap):
    """Align sockets as closely as possible while keeping node bounds apart."""
    nodes = sorted(nodes, key=lambda node: (desired[node], order[node]))
    offsets, blocks, cursor = {}, [], 0.
    for node in nodes:
        offsets[node] = cursor
        blocks.append(([node], desired[node] - cursor, 1))
        cursor += sizes[node][1] + gap
        # Isotonic regression spreads collisions above and below their ideal
        # positions, instead of pushing every branch farther down the graph.
        while len(blocks) > 1 and blocks[-2][1] > blocks[-1][1]:
            right, left = blocks.pop(), blocks.pop()
            weight = left[2] + right[2]
            blocks.append((left[0] + right[0], (left[1] * left[2] + right[1] * right[2]) / weight, weight))
    return {node: mean + offsets[node] for members, mean, _ in blocks for node in members}


def layout_nodes(sizes, edges, anchor="__output__", origin=(365., 40.), horizontal_gap=110., vertical_gap=64.):
    """Return positions for all nodes, keeping the scene-output node fixed.

    Sizes include sockets and node decoration. Edges are (source, target,
    source_socket_y, target_socket_y), with socket offsets local to each node.
    """
    if not sizes:
        return {}
    order = {node: i for i, node in enumerate(sizes)}
    incoming, outgoing, neighbors = defaultdict(list), defaultdict(list), defaultdict(set)
    for source, target, source_y, target_y in edges:
        outgoing[source].append((target, source_y, target_y))
        incoming[target].append((source, source_y, target_y))
        neighbors[source].add(target)
        neighbors[target].add(source)

    indegree = {node: len(incoming[node]) for node in sizes}
    ready = deque(node for node in sizes if not indegree[node])
    topology = []
    while ready:
        node = ready.popleft()
        topology.append(node)
        for child, _, _ in outgoing[node]:
            indegree[child] -= 1
            if not indegree[child]:
                ready.append(child)
    if len(topology) != len(sizes):
        raise ValueError("Cannot lay out cyclic shader connections")
    distance = {}
    for node in reversed(topology):
        distance[node] = max((distance[child] + 1 for child, _, _ in outgoing[node]), default=0)

    components, seen = [], set()
    for seed in ([anchor] if anchor in sizes else []) + list(sizes):
        if seed in seen:
            continue
        component, pending = [], [seed]
        seen.add(seed)
        while pending:
            node = pending.pop()
            component.append(node)
            for neighbor in sorted(neighbors[node], key=order.get):
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        components.append(sorted(component, key=order.get))

    def arrange(component):
        depth = max(distance[node] for node in component)
        columns = [[] for _ in range(depth + 1)]
        for node in component:
            columns[depth - distance[node]].append(node)
        ys = {node: 0. for node in component}

        def align(column, links, forward):
            desired = {}
            for node in column:
                targets = [ys[other] + source_y - target_y if forward else ys[other] + target_y - source_y
                           for other, source_y, target_y in links[node]]
                desired[node] = sum(targets) / len(targets) if targets else ys[node]
            ys.update(_pack_column(column, desired, sizes, order, vertical_gap))

        # Work back from the sinks first; alternate sweeps then group branches
        # around shared inputs and respect the order of the destination sockets.
        for column in reversed(columns):
            align(column, outgoing, False)
        for _ in range(3):
            for column in columns[1:]:
                if anchor not in column:
                    align(column, incoming, True)
            for column in reversed(columns[:-1]):
                align(column, outgoing, False)
        x, positions = 0., {}
        for column in columns:
            for node in column:
                positions[node] = [x, ys[node]]
            x += max(sizes[node][0] for node in column) + horizontal_gap
        return positions

    def bounds(positions):
        return (min(p[0] for p in positions.values()), min(p[1] for p in positions.values()),
                max(p[0] + sizes[node][0] for node, p in positions.items()),
                max(p[1] + sizes[node][1] for node, p in positions.items()))

    positions = {}
    if anchor in sizes:
        main = arrange(components.pop(0))
        dx, dy = origin[0] - main[anchor][0], origin[1] - main[anchor][1]
        positions.update({node: [x + dx, y + dy] for node, (x, y) in main.items()})
        left, top, right, bottom = bounds(positions)
        shelf_width = max(right - left, 3 * (max(w for w, _ in sizes.values()) + horizontal_gap) - horizontal_gap)
        if len(main) == 1:
            # With no assigned outputs, keep loose nodes beside the output.
            right, top = origin[0] - horizontal_gap, origin[1]
        else:
            top = bottom + 2 * vertical_gap
        left = right - shelf_width
    else:
        shelf_width = 3 * (max(w for w, _ in sizes.values()) + horizontal_gap) - horizontal_gap
        left, top = origin

    # Pack disconnected networks as separate blocks. Loose single nodes form
    # rows instead of an arbitrarily tall stack in one column.
    blocks = [arrange(component) for component in components]
    required_width = max((bounds(block)[2] - bounds(block)[0] for block in blocks), default=0.)
    if required_width > shelf_width:
        if anchor in sizes:
            left -= required_width - shelf_width
        shelf_width = required_width
    x, y, row_height = left, top, 0.
    for block in blocks:
        x0, y0, x1, y1 = bounds(block)
        width, height = x1 - x0, y1 - y0
        if x > left and x - left + width > shelf_width:
            x, y, row_height = left, y + row_height + 2 * vertical_gap, 0.
        positions.update({node: [px - x0 + x, py - y0 + y] for node, (px, py) in block.items()})
        x += width + horizontal_gap
        row_height = max(row_height, height)
    return positions
