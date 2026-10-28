import os
from typing import Union

from jinja2 import Environment, FileSystemLoader


def render_template(
    template_name: Union[str, os.PathLike],
    variables: dict,
    template_dir: Union[str, os.PathLike] = None,
) -> str:
    """
    Renders a Jinja2 template with the given variables.

    Parameters:
        template_name (str | os.PathLike): Name of the template file
        variables (dict): Dictionary of variables to render in the template
        template_dir (str | os.PathLike): Directory where templates are stored

    Returns:
        str: Rendered HTML string
    """
    if template_dir is None:
        # Make template_dir relative to this file's location
        template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(str(template_name))
    return template.render(**variables)
