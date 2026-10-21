import pytest
from channels.layers import get_channel_layer


@pytest.fixture(autouse=True)
async def _fresh_channel_layer():
    """Each test runs in its own event loop; drop that loop's Redis connections after."""
    yield
    layer = get_channel_layer()
    flush = getattr(layer, "flush", None)
    if flush is not None:
        await flush()
