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


async def close_models(*models):
    # PyRIT 1.1 exposes no public async close method on OpenAIChatTarget.
    for model in models:
        client = getattr(model, "_async_client", None)
        if client is not None:
            await client.close()
