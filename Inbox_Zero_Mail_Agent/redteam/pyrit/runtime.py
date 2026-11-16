"""Keep PyRIT's import-time cache and logs inside this integration."""
from unittest.mock import patch

import appdirs

from settings import HERE


async def initialize():
    original = appdirs.user_data_dir

    def local_data(appname=None, appauthor=None, *args, **kwargs):
        if appauthor == "pyrit":
            return str(HERE / ".pyrit" / appname)
        return original(appname, appauthor, *args, **kwargs)

    # PyRIT 1.1 has no data-directory setting; these paths are fixed at import.
    with patch.object(appdirs, "user_data_dir", local_data):
        from pyrit.setup import initialize_pyrit_async
    await initialize_pyrit_async("InMemory", load_defaults=False, env_files=[], silent=True)
    import os
    if os.getenv("MAIL_EXPERIMENT_USAGE"):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(HERE.parent.parent / "experiment/scripts"))
        from usage import install_pyrit_usage_hook
        install_pyrit_usage_hook(Path(os.environ["MAIL_EXPERIMENT_USAGE"]))


async def close_models(*models):
    # PyRIT 1.1 exposes no public async close method on OpenAIChatTarget.
    for model in models:
        client = getattr(model, "_async_client", None)
        if client is not None:
            await client.close()
