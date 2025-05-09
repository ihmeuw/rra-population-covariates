import click

from rra_population_covariates import process


@click.group()
def pcrun() -> None:
    """Run a stage of the population covariates pipeline."""


@click.group()
def pctask() -> None:
    """Run an individual task in the population covariates pipeline."""


for module in [process]:
    runners = getattr(module, "RUNNERS", {})
    task_runners = getattr(module, "TASK_RUNNERS", {})

    if not runners or not task_runners:
        continue

    command_name = module.__name__.split(".")[-1]

    @click.group(name=command_name)
    def workflow_runner() -> None:
        pass

    for name, runner in runners.items():
        workflow_runner.add_command(runner, name)

    pcrun.add_command(workflow_runner)

    @click.group(name=command_name)
    def task_runner() -> None:
        pass

    for name, runner in task_runners.items():
        task_runner.add_command(runner, name)

    pctask.add_command(task_runner)
