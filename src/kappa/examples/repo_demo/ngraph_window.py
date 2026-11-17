def run(hetr):
    hetr.register_graph_pass(ngraph.transformers.passes.nviz.VizPass(show_all_metadata=True))
