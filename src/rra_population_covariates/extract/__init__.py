from rra_population_covariates.extract.open_building_map import (
    open_building_map,
    open_building_map_task,
)
from rra_population_covariates.extract.open_building_map_label_source import (
    open_building_map_label_source,
    open_building_map_label_source_task,
    open_building_map_overture_lookup_task,
)

RUNNERS = {
    "open_building_map": open_building_map,
    "open_building_map_label_source": open_building_map_label_source,
}
TASK_RUNNERS = {
    "open_building_map": open_building_map_task,
    "open_building_map_label_source": open_building_map_label_source_task,
    "open_building_map_overture_lookup": open_building_map_overture_lookup_task,
}
