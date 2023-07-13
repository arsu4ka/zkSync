import aiohttp
from eth_abi import encode
from eth_account.signers.local import LocalAccount
from loguru import logger
from web3 import Web3

from utils.helper import (
    approve_token,
    create_amount,
    get_wallet_balance,
    load_contract,
)


class Staker:
    def __init__(self,
                 private_key: str,
                 node: str,
                 slippage_percent: int,
                 deadline_minutes: int,
                 tokens: dict[str, str],
                 ):
        self.web3 = Web3(Web3.HTTPProvider(node))
        self.private_key = private_key
        self.account: LocalAccount = self.web3.eth.account.from_key(self.private_key)
        self.address_wallet = self.account.address
        self.slippage_percent = slippage_percent
        self.deadline_minutes = deadline_minutes
        self.tokens = tokens

    async def calc_slippage(self, value: int) -> int:
        return int(value * (1 - (self.slippage_percent / 100)))

    async def get_deadline(self) -> int:
        import datetime
        import time
        return int(time.time() + datetime.timedelta(minutes=self.deadline_minutes).total_seconds())

    @staticmethod
    async def get_relative_amount(
                                  token1_amount: float,
                                  token1_reserve: float,
                                  token2_reserve: float
                                  ) -> float:
        token1_reserve, token2_reserve = float(token1_reserve), float(token2_reserve)
        total = token1_reserve + token2_reserve
        token1_percent = token1_reserve / total
        token2_amount = (token1_amount / token1_percent) - token1_amount
        return token2_amount

    @staticmethod
    async def sync_swap_pool_data(pool_address: str, wallet_address: str):
        params = {
            'network': 'zkSyncMainnet',
            'account': wallet_address,
            'quote': 'next',
        }
        async with aiohttp.ClientSession() as session:
            response = await session.get('https://api.syncswap.xyz/api/fetchers/fetchAllPools', params=params)
            response.raise_for_status()
            res_json = await response.json()

        for pool in res_json["pools"]:
            if pool["pool"] == pool_address:
                return pool
        return None

    @staticmethod
    async def kyber_swap_pool_data(pool_address: str, pools_number: int = 26):
        if pools_number > 105:
            return None

        json_data = {
            'operationName': 'poolsPagination',
            'variables': {},
            'query': 'query poolsPagination {\n  pools(first: 26, skip: 0, subgraphError: allow) {\n    id\n    '
                     'txCount\n    token0 {\n      id\n      symbol\n      name\n      decimals\n      '
                     'totalLiquidity\n      derivedETH\n    }\n    token1 {\n      id\n      symbol\n      name\n     '
                     ' decimals\n      totalLiquidity\n      derivedETH\n    }\n    amp\n    reserve0\n    reserve1\n '
                     '   vReserve0\n    vReserve1\n    reserveUSD\n    totalSupply\n    trackedReserveETH\n    '
                     'reserveETH\n    volumeUSD\n    fee\n    feeUSD\n    untrackedVolumeUSD\n    untrackedFeeUSD\n   '
                     ' token0Price\n    token1Price\n    token0PriceMin\n    token0PriceMax\n    token1PriceMin\n    '
                     'token1PriceMax\n    createdAtTimestamp\n  }\n}\n',
        }
        json_data["query"].replace("26", str(pools_number))

        async with aiohttp.ClientSession() as session:
            response = await session.post(
                'https://zksync-graph.kyberengineering.io/subgraphs/name/kybernetwork/kyberswap-exchange-zksync',
                json=json_data
            )
            response.raise_for_status()
            res_json = await response.json()

        for pool in res_json["data"]["pools"]:
            if pool["id"] == pool_address:
                return pool

        return await Staker.kyber_swap_pool_data(pool_address, pools_number * 2)

    async def sync_swap(self,
                        pool_address: str,
                        router_address: str,
                        router_abi: str,
                        token1_amount: float,
                        token2_amount: float
                        ):

        ### Retrieving tokens addresses
        pool_data = await Staker.sync_swap_pool_data(pool_address, self.address_wallet)
        token1_symbol = str(pool_data["token0"]["symbol"]).upper()
        token2_symbol = str(pool_data["token1"]["symbol"]).upper()
        if token1_symbol == "WETH":
            token1_symbol = "ETH"
        if token2_symbol == "WETH":
            token2_symbol = "ETH"
        token1_address = str(pool_data["token0"]["token"])
        token2_address = str(pool_data["token1"]["token"])
        ###

        ### Getting router contract
        router_contract = await load_contract(
            address=Web3.to_checksum_address(router_address),
            web3=self.web3,
            abi_name=router_abi
        )
        ###

        ### Verifying TOKEN1 balance
        token1_amount_wei, token1_contract = await create_amount(token1_symbol, self.web3, token1_address,
                                                                 token1_amount)
        token1_amount_wei = int(token1_amount_wei)
        token1_balance = await get_wallet_balance(token1_symbol, self.web3, self.address_wallet, token1_contract,
                                                  "ERA")
        if token1_amount_wei > token1_balance:
            logger.error(f'Not enough {token1_symbol} on wallet {self.address_wallet}')
            return
        ###

        ### Verifying TOKEN2 balance
        token2_amount_wei, token2_contract = await create_amount(token2_symbol, self.web3, token2_address,
                                                                 token2_amount)
        token2_amount_wei = int(token2_amount_wei)
        token2_balance = await get_wallet_balance(token2_symbol, self.web3, self.address_wallet, token2_contract,
                                                  "ERA")
        if token2_amount_wei > token2_balance:
            logger.error(f'Not enough {token2_symbol} on wallet {self.address_wallet}')
            return
        ###

        callback = "0x0000000000000000000000000000000000000000"

        ### Approving TOKEN1
        if token1_symbol != "ETH":
            await approve_token(
                amount=token1_amount_wei,
                private_key=self.private_key,
                chain="ERA",
                from_token_address=token1_address,
                from_token_symbol=token1_symbol,
                spender=router_address,
                address_wallet=self.address_wallet,
                web3=self.web3
            )
        ###

        ### Approving TOKEN2
        if token2_symbol != "ETH":
            await approve_token(
                amount=token2_amount_wei,
                private_key=self.private_key,
                chain="ERA",
                from_token_address=token2_address,
                from_token_symbol=token2_symbol,
                spender=router_address,
                address_wallet=self.address_wallet,
                web3=self.web3
            )
        ###

        ### Calculating ETH value for building transaction
        if token1_symbol == "ETH":
            trans_value = token1_amount_wei
            call_data = [
                [Web3.to_checksum_address(callback), token1_amount_wei],
                [Web3.to_checksum_address(token2_address), 0]
            ]
        elif token2_symbol == "ETH":
            trans_value = token2_amount_wei
            call_data = [
                [Web3.to_checksum_address(callback), token2_amount_wei],
                [Web3.to_checksum_address(token1_address), 0]
            ]
        else:
            trans_value = 0
            call_data = [
                [Web3.to_checksum_address(token1_address), token1_amount_wei]
            ]
        ###

        ### Calling addLiquidity and building transaction
        tx = router_contract.functions.addLiquidity(
            Web3.to_checksum_address(pool_address),
            call_data,
            encode(["address"], [self.address_wallet]),
            0,
            Web3.to_checksum_address(callback),
            '0x'
        ).build_transaction({
            'from': self.address_wallet,
            'value': trans_value,
            'nonce': self.web3.eth.get_transaction_count(self.address_wallet),
            'maxFeePerGas': 0,
            'maxPriorityFeePerGas': 0,
            'gas': 0
        })
        ###

        ### updating tx
        tx.update({'maxFeePerGas': self.web3.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3.eth.gas_price})
        gas_limit = self.web3.eth.estimate_gas(tx)
        tx.update({'gas': gas_limit})
        ###

        ### signing transaction
        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)
        logger.success(
            f'Added {token1_amount} {token1_symbol}, {token2_amount} {token2_symbol} tokens to liquidity pool | TX: '
            f'https://explorer.zksync.io/tx/{tx_hash}')
        ###

    async def kyber_swap(self,
                         router_address: str,
                         abi_file_name: str,
                         pool_address: str,
                         token1_amount: float,
                         ) -> None:

        ### Getting pool data and router contract details
        pool_data = await Staker.kyber_swap_pool_data(pool_address)
        router_contract = await load_contract(router_address, self.web3, abi_file_name)
        ###

        ### Retrieving tokens addresses
        token1_symbol = str(pool_data["token0"]["symbol"]).upper()
        token2_symbol = str(pool_data["token1"]["symbol"]).upper()
        if token1_symbol == "WETH":
            token1_symbol = "ETH"
        if token2_symbol == "WETH":
            token2_symbol = "ETH"
        token1_address = str(pool_data["token0"]["id"])
        token2_address = str(pool_data["token1"]["id"])
        ###

        ### Verifying TOKEN1 balance
        token1_amount_wei, token1_contract = await create_amount(token1_symbol, self.web3, token1_address,
                                                                 token1_amount)
        token1_amount_wei = int(token1_amount_wei)
        token1_balance = await get_wallet_balance(token1_symbol, self.web3, self.address_wallet, token1_contract,
                                                  "ERA")
        if token1_amount_wei > token1_balance:
            logger.error(f'Not enough {token1_symbol} on wallet {self.address_wallet}')
            return
        ###

        ### Verifying TOKEN2 balance
        token2_amount = await Staker.get_relative_amount(token1_amount, pool_data["reserve0"], pool_data["reserve1"])
        token2_amount_wei, token2_contract = await create_amount(token2_symbol, self.web3, token2_address,
                                                                 token2_amount)
        token2_amount_wei = int(token2_amount_wei)
        token2_balance = await get_wallet_balance(token2_symbol, self.web3, self.address_wallet, token2_contract,
                                                  "ERA")
        if token2_amount_wei > token2_balance:
            logger.error(f'Not enough {token2_symbol} on wallet {self.address_wallet}')
            return
        ###

        ### Approving TOKEN1
        if token1_symbol != "ETH":
            await approve_token(
                amount=token1_amount_wei,
                private_key=self.private_key,
                chain="ERA",
                from_token_address=token1_address,
                from_token_symbol=token1_symbol,
                spender=router_address,
                address_wallet=self.address_wallet,
                web3=self.web3
            )
        ###

        ### Approving TOKEN2
        if token2_symbol != "ETH":
            await approve_token(
                amount=token2_amount_wei,
                private_key=self.private_key,
                chain="ERA",
                from_token_address=token2_address,
                from_token_symbol=token2_symbol,
                spender=router_address,
                address_wallet=self.address_wallet,
                web3=self.web3
            )
        ###

        ### Calculating ETH value for building transaction
        if token1_symbol == "ETH":
            trans_value = token1_amount_wei
        elif token2_symbol == "ETH":
            trans_value = token2_amount_wei
        else:
            trans_value = 0
        ###

        ### Calculating vReserveRatioBounds (vReserveB/vReserveA limits)
        # current_diff = float(pool_data["vReserve1"]) / float(pool_data["vReserve0"])
        # current_diff_with_precision = int(current_diff * (2 ** 112))
        # left_limit = int(current_diff_with_precision * (1 - (self.slippage_percent / 100)))
        # right_limit = int(current_diff_with_precision * (1 + (self.slippage_percent / 100)))
        # bounds = [left_limit, right_limit]
        bounds = [
            0,
            100000000000000000000000000000000000000000000000000000000000000000000000000000
        ]
        ###

        ### Calling addLiquidity and building transaction
        if token2_symbol == "ETH":
            tx = router_contract.functions.addLiquidityETH(
                Web3.to_checksum_address(token1_address),
                Web3.to_checksum_address(pool_address),
                token1_amount_wei,
                await self.calc_slippage(token1_amount_wei),
                await self.calc_slippage(token2_amount_wei),
                bounds,
                self.address_wallet,
                await self.get_deadline()
            ).build_transaction({
                'from': self.address_wallet,
                'value': token2_amount_wei,
                'nonce': self.web3.eth.get_transaction_count(self.address_wallet),
                'maxFeePerGas': 0,
                'maxPriorityFeePerGas': 0,
                'gas': 0
            })
        else:
            tx = router_contract.functions.addLiquidity(
                Web3.to_checksum_address(token1_address),
                Web3.to_checksum_address(token2_address),
                Web3.to_checksum_address(pool_address),
                token1_amount_wei,
                token2_amount_wei,
                await self.calc_slippage(token1_amount_wei),
                await self.calc_slippage(token2_amount_wei),
                bounds,
                self.address_wallet,
                await self.get_deadline()
            ).build_transaction({
                'from': self.address_wallet,
                'value': trans_value,
                'nonce': self.web3.eth.get_transaction_count(self.address_wallet),
                'maxFeePerGas': 0,
                'maxPriorityFeePerGas': 0,
                'gas': 0
            })
        ###

        ### updating tx
        tx.update({'maxFeePerGas': self.web3.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3.eth.gas_price})
        gas_limit = self.web3.eth.estimate_gas(tx)
        tx.update({'gas': gas_limit})
        ###

        ### signing transaction
        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)
        logger.success(
            f'Added {token1_amount} {token1_symbol}, {token2_amount} {token2_symbol} tokens to liquidity '
            f'pool | TX:'
            f'https://explorer.zksync.io/tx/{tx_hash}')
        ###
