"""The tasks product's manifest."""

from bowerstone_engine.products import Permission, ProductManifest
from bowerstone_example_tasks.routes import build_router

PRODUCT = ProductManifest(
    key="tasks",
    name="Tasks",
    version="0.1.0",
    migrations_package="bowerstone_example_tasks.migrations",
    router=build_router,
    requires=("notes",),
    engine_versions=">=0.0,<0.1",
    ui_versions=">=0.0,<0.1",
    permissions=(
        Permission("tasks.view", "View tasks", ("owner", "staff", "read_only")),
        Permission("tasks.edit", "Add and finish tasks", ("owner", "staff")),
    ),
    data_deletable=False,
)
