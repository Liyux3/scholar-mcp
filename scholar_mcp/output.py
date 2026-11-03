"""Text serialization shared by the CLI and MCP boundary."""

import yaml


class _YamlDumper(yaml.SafeDumper):
    """Write multi-line text as literal blocks.

    The default folded quoting doubles every newline in paper text; literal
    blocks read naturally and measured about 6% fewer tokens on real reads.
    """


_YamlDumper.add_representer(str, lambda dumper, value: dumper.represent_scalar(
    "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None))


def yaml_text(data) -> str:
    return yaml.dump(data, Dumper=_YamlDumper, default_flow_style=False,
                     allow_unicode=True, sort_keys=False)
