from rra_population_covariates.process.open_building_map import (
    open_building_map,
    open_building_map_task,
)
from rra_population_covariates.process.overture_roads import (
    overture_roads,
    overture_roads_task,
)
from rra_population_covariates.process.overture_water import (
    overture_water,
    overture_water_task,
)

RUNNERS = {
    "open_building_map": open_building_map,
    "overture_roads": overture_roads,
    "overture_water": overture_water,
}
TASK_RUNNERS = {
    "open_building_map": open_building_map_task,
    "overture_roads": overture_roads_task,
    "overture_water": overture_water_task,
}
