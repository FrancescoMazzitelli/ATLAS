
from synthesize import load_config
from graphviz import Digraph
from pathlib import Path

def survey_logic_viz(config_folder: str, write_to_disk: bool = False, output_path: str = 'flowchart'):
    _, _, survey_conf, _ = load_config(config_folder)

    logic = survey_conf["logic"]
    dot = Digraph(format='png')
    dot.attr(rankdir='LR')  # Left-to-right flow

    # Build the graph
    for key, value in logic.items():
        if isinstance(value, dict):
            for condition, next_node in value.items():
                if next_node:
                    label = f"{key} == {condition}" if condition != "ELSE" else "ELSE"
                    dot.edge(key, next_node, label=label)
        elif value:
            dot.edge(key, value)

    if write_to_disk:
        filepath = Path(dot.render(output_path, view=False))
        print(f"Graph written to: {filepath.resolve()}")
    else:
        dot.view()


def main():
    config_folder = "configs/Chicago"
    survey_logic_viz(config_folder)

if __name__ == "__main__":
    main()