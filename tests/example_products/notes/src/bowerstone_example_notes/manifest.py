"""The notes product's manifest."""

from bowerstone_engine.products import Permission, ProductManifest
from bowerstone_example_notes.routes import build_router

PRODUCT = ProductManifest(
    key="notes",
    name="Notes",
    version="0.1.0",
    migrations_package="bowerstone_example_notes.migrations",
    router=build_router,
    requires=(),
    engine_versions=">=0.0,<0.1",
    ui_versions=">=0.0,<0.1",
    permissions=(
        Permission("notes.view", "View notes", ("owner", "staff", "read_only")),
        Permission("notes.edit", "Write and delete notes", ("owner", "staff")),
    ),
    data_deletable=True,
)
