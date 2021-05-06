import demo_registrydao.models as models
from demo_registrydao.types.registry.parameter.propose import ProposeParameter
from demo_registrydao.types.registry.storage import RegistryStorage
from dipdup.models import OperationHandlerContext, OriginationContext, TransactionContext


async def on_propose(
    ctx: OperationHandlerContext,
    propose: TransactionContext[ProposeParameter, RegistryStorage],
) -> None:
    ...
