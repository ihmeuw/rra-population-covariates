from rra_population_covariates.process.overture_roads import (
    overture_roads,
    overture_roads_task,
)
from rra_population_covariates.process.overture_water import (
    overture_water,
    overture_water_task,
)

RUNNERS = {"overture_roads": overture_roads, "overture_water": overture_water}
TASK_RUNNERS = {
    "overture_roads": overture_roads_task,
    "overture_water": overture_water_task,
}
