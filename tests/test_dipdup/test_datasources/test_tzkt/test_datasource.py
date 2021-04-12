import json
from os.path import dirname, join
from unittest import IsolatedAsyncioTestCase
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

from pydantic import BaseModel
from signalrcore.hub.base_hub_connection import BaseHubConnection  # type: ignore
from signalrcore.transport.websockets.connection import ConnectionState  # type: ignore
from tortoise import Tortoise

from dipdup.config import ContractConfig, OperationHandlerConfig, OperationHandlerPatternConfig, OperationIndexConfig
from dipdup.datasources.tzkt.datasource import TzktDatasource
from dipdup.models import HandlerContext, IndexType, OperationContext, OperationData, State


class Collect(BaseModel):
    objkt_amount: str
    swap_id: str


class TzktDatasourceTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.index_config = OperationIndexConfig(
            kind='operation',
            datasource='tzkt',
            contract=ContractConfig(address='KT1lalala'),
            handlers=[
                OperationHandlerConfig(
                    callback='',
                    pattern=[
                        OperationHandlerPatternConfig(
                            destination=ContractConfig(address='KT1Hkg5qeNhfwpKW4fXvq7HGZB9z2EnmCCA9'), entrypoint='collect'
                        )
                    ],
                )
            ],
        )
        self.index_config.state = State(index_name='test', index_type=IndexType.operation, hash='')
        self.index_config.handlers[0].pattern[0].parameter_type_cls = Collect
        self.datasource = TzktDatasource('tzkt.test')
        self.datasource.add_index(self.index_config)

    async def test_convert_operation(self):
        with open(join(dirname(__file__), 'operations.json')) as file:
            operations_message = json.load(file)

        for operation_json in operations_message['data']:
            operation = TzktDatasource.convert_operation(operation_json)
            self.assertIsInstance(operation, OperationData)

    async def test_get_client(self):
        client = self.datasource._get_client()
        self.assertIsInstance(client, BaseHubConnection)
        self.assertEqual(self.datasource.on_connect, client.transport._on_open)

    async def test_start(self):
        client = self.datasource._get_client()
        client.start = AsyncMock()

        fetch_operations_mock = AsyncMock()
        self.datasource.fetch_operations = fetch_operations_mock

        get_mock = MagicMock()
        get_mock.return_value.__aenter__.return_value.json.return_value = [{'level': 1337}]

        with patch('aiohttp.ClientSession.get', get_mock):
            await self.datasource.start()

        fetch_operations_mock.assert_awaited_with(0)
        self.assertEqual({self.index_config.contract: ['transaction']}, self.datasource._subscriptions)
        client.start.assert_awaited()

    async def test_on_connect_subscribe_to_operations(self):
        send_mock = AsyncMock()
        client = self.datasource._get_client()
        client.send = send_mock
        client.transport.state = ConnectionState.connected
        self.datasource._subscriptions = {
            self.index_config.contract.address: ['transaction'],
            'some_contract': ['transaction'],
        }

        await self.datasource.on_connect()

        send_mock.assert_has_awaits(
            [
                call('SubscribeToOperations', [{'address': self.index_config.contract.address, 'types': 'transaction'}]),
                call('SubscribeToOperations', [{'address': 'some_contract', 'types': 'transaction'}]),
            ]
        )
        self.assertEqual(2, len(client.handlers))

    async def test_on_fetch_operations(self):
        self.datasource._subscriptions = {self.index_config.contract.address: ['transaction']}
        with open(join(dirname(__file__), 'operations.json')) as file:
            operations_message = json.load(file)
            del operations_message['state']
        stripped_operations_message = operations_message['data']

        on_operation_message_mock = AsyncMock()
        get_mock = MagicMock()
        get_mock.return_value.__aenter__.return_value.json.return_value = stripped_operations_message

        self.datasource.on_operation_message = on_operation_message_mock

        with patch('aiohttp.ClientSession.get', get_mock):
            await self.datasource.fetch_operations(1337)

        on_operation_message_mock.assert_awaited_with(
            address=self.index_config.contract.address,
            message=[operations_message],
            sync=True,
        )

    async def test_on_operation_message_state(self):
        fetch_operations_mock = AsyncMock()
        self.datasource.fetch_operations = fetch_operations_mock

        await self.datasource.on_operation_message([{'type': 0, 'state': 123}], self.index_config.contract.address)
        fetch_operations_mock.assert_awaited_with(123)

    async def test_on_operation_message_data(self):
        with open(join(dirname(__file__), 'operations.json')) as file:
            operations_message = json.load(file)
        operations = [TzktDatasource.convert_operation(op) for op in operations_message['data']]
        operation = TzktDatasource.convert_operation(operations_message['data'][-2])

        on_operation_match_mock = AsyncMock()
        self.datasource.on_operation_match = on_operation_match_mock

        try:
            await Tortoise.init(
                db_url='sqlite://:memory:',
                modules={
                    'int_models': ['dipdup.models'],
                },
            )
            await Tortoise.generate_schemas()

            await self.datasource.on_operation_message([operations_message], self.index_config.contract.address, sync=True)

            on_operation_match_mock.assert_awaited_with(
                self.index_config,
                self.index_config.handlers[0],
                [operation],
                ANY,
            )

        finally:
            await Tortoise.close_connections()

    async def test_on_operation_match(self):
        with open(join(dirname(__file__), 'operations.json')) as file:
            operations_message = json.load(file)
        operations = [TzktDatasource.convert_operation(op) for op in operations_message['data']]
        matched_operation = operations[0]

        try:
            await Tortoise.init(
                db_url='sqlite://:memory:',
                modules={
                    'int_models': ['dipdup.models'],
                },
            )
            await Tortoise.generate_schemas()

            callback_mock = AsyncMock()

            self.index_config.handlers[0].callback_fn = callback_mock

            self.datasource._synchronized.set()
            await self.datasource.on_operation_match(self.index_config, self.index_config.handlers[0], [matched_operation], operations)

            self.assertIsInstance(callback_mock.await_args[0][0], HandlerContext)
            self.assertIsInstance(callback_mock.await_args[0][1], OperationContext)
            self.assertIsInstance(callback_mock.await_args[0][1].parameter, Collect)
            self.assertIsInstance(callback_mock.await_args[0][1].data, OperationData)

        finally:
            await Tortoise.close_connections()