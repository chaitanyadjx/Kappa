import ngraph as ng


def run(hetr):
    hetr.register_graph_pass(ng.transformers.passes.nviz.VizPass(show_all_metadata=True))
