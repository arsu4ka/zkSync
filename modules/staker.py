import asyncio
import aiohttp
from eth_abi import encode
from eth_account.signers.local import LocalAccount
from loguru import logger
from web3 import Web3

import swapper_config as cnf
from helper import (
    approve_token,
    setup_for_liq,
    load_abi,
    create_amount,
    get_wallet_balance,
)


class Staker:
    def __init__(self,
                 private_key: str,
                 node: str = cnf.node,
                 slippage_percent: int = cnf.slippage_percent,
                 deadline_minutes: int = cnf.deadline_minutes
                 ):
        self.web3 = Web3(Web3.HTTPProvider(node))
        self.private_key = private_key
        self.account: LocalAccount = self.web3.eth.account.from_key(self.private_key)
        self.address_wallet = self.account.address
        self.slippage_percent = slippage_percent
        self.deadline_minutes = deadline_minutes

    async def calc_slippage(self, value: int) -> int:
        return int(value * (1 - (self.slippage_percent / 100)))

    async def get_deadline(self) -> int:
        import datetime
        import time
        return int(time.time() + datetime.timedelta(minutes=self.deadline_minutes).total_seconds())

    @staticmethod
    async def get_relative_amount(pool_data: dict, token1_amount: float) -> float:
        token1_reserve = float(pool_data["reserve0"])
        token2_reserve = float(pool_data["reserve1"])
        total = token1_reserve + token2_reserve
        token1_percent = token1_reserve / total
        token2_amount = (token1_amount / token1_percent) - token1_amount
        return token2_amount

    @staticmethod
    async def kyber_swap_pool_data(pool_address: str, pools_number: int = 26):
        if pools_number > 105:
            return None

        json_data = {
            'operationName': 'poolsPagination',
            'variables': {},
            'query': 'query poolsPagination {\n  pools(first: 26, skip: 0, subgraphError: allow) {\n    id\n    txCount\n    token0 {\n      id\n      symbol\n      name\n      decimals\n      totalLiquidity\n      derivedETH\n    }\n    token1 {\n      id\n      symbol\n      name\n      decimals\n      totalLiquidity\n      derivedETH\n    }\n    amp\n    reserve0\n    reserve1\n    vReserve0\n    vReserve1\n    reserveUSD\n    totalSupply\n    trackedReserveETH\n    reserveETH\n    volumeUSD\n    fee\n    feeUSD\n    untrackedVolumeUSD\n    untrackedFeeUSD\n    token0Price\n    token1Price\n    token0PriceMin\n    token0PriceMax\n    token1PriceMin\n    token1PriceMax\n    createdAtTimestamp\n  }\n}\n',
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

    async def sync_swap_staking(self, token: str, amount: float):
        classic_pool_factory_address = '0xf2DAd89f2788a8CD54625C60b55cD3d2D0ACa7Cb'
        router_address = '0x2da10A1e27bF85cEdD8FFb1AbBe97e53391C0295'

        to_token_address, from_token_address = await setup_for_liq(token)

        classic_pool_factory = self.web3.eth.contract(
            address=Web3.to_checksum_address(classic_pool_factory_address),
            abi=await load_abi('classic_pool_factory_address'))
        pool_address = classic_pool_factory.functions.getPool(Web3.to_checksum_address(from_token_address),
                                                              Web3.to_checksum_address(to_token_address)).call()

        value, token_contract = await create_amount(token, self.web3, from_token_address, amount)
        value = int(value)

        balance = await get_wallet_balance(token, self.web3, self.address_wallet, token_contract, 'ERA')

        if pool_address == "0x0000000000000000000000000000000000000000":
            logger.error('Pool does not exist')
            return

        if value > balance:
            logger.error(f'Not enough money for wallet {self.address_wallet}')
            return

        native_eth_address = "0x0000000000000000000000000000000000000000"

        min_liquidity = 0
        callback = native_eth_address

        router = self.web3.eth.contract(address=Web3.to_checksum_address(router_address),
                                        abi=await load_abi('sync_swap_router'))

        if token.lower() != 'eth':
            await approve_token(amount=value,
                                private_key=self.private_key,
                                chain='ERA',
                                from_token_address=from_token_address,
                                spender=router_address,
                                address_wallet=self.address_wallet,
                                web3=self.web3)

        data = encode(
            ["address"],
            [self.address_wallet]
        )

        tx = router.functions.addLiquidity2(
            Web3.to_checksum_address(pool_address),
            [(Web3.to_checksum_address(to_token_address), 0),
             (Web3.to_checksum_address(callback), value)] if token.lower() == 'eth' else [
                (Web3.to_checksum_address(from_token_address), value)],
            data,
            min_liquidity,
            callback,
            '0x'
        ).build_transaction({
            'from': self.address_wallet,
            'value': value if token.lower() == 'eth' else 0,
            'nonce': self.web3.eth.get_transaction_count(self.address_wallet),
            'maxFeePerGas': 0,
            'maxPriorityFeePerGas': 0,
            'gas': 0
        })
        tx.update({'maxFeePerGas': self.web3.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3.eth.gas_price})

        gas_limit = self.web3.eth.estimate_gas(tx)
        tx.update({'gas': gas_limit})

        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)
        logger.success(
            f'Added {amount} {token} tokens to liquidity pool | TX: https://explorer.zksync.io/tx/{tx_hash}')

    async def kyber_swap_usdc_eth(self, usdc_amount: float):
        usdc_symbol, eth_symbol = "USDC", "ETH"
        usdc_address, eth_address = cnf.tokens[usdc_symbol.upper()], cnf.tokens[eth_symbol.upper()]

        pool_address = "0xD1b46742fF88abd8D2E3312c283DdEa5f52B3360"
        data = await Staker.kyber_swap_pool_data(pool_address)
        router_address = "0x937f4f2FF1889b79dAa08debfCA5C237a07A5208"
        router_contract = self.web3.eth.contract(address=Web3.to_checksum_address(router_address),
                                                 abi=await load_abi('kyber_swap_router'))

        ### Verifying USDC balance
        usdc_amount_wei, usdc_contract = await create_amount(usdc_symbol, self.web3, usdc_address, usdc_amount)
        usdc_amount_wei = int(usdc_amount_wei)
        usdc_balance = await get_wallet_balance(usdc_symbol, self.web3, self.address_wallet, usdc_contract, 'ERA')
        if usdc_amount_wei > usdc_balance:
            logger.error(f'Not enough USDC on wallet {self.address_wallet}')
            return
        ###

        ### Verifying ETH balance
        eth_amount = await Staker.get_relative_amount(data, usdc_amount)
        eth_amount_wei, eth_contract = await create_amount(eth_symbol, self.web3, eth_address, eth_amount)
        eth_amount_wei = int(eth_amount_wei)
        eth_balance = await get_wallet_balance(eth_symbol, self.web3, self.address_wallet, eth_contract, "ERA")
        if eth_amount_wei > eth_balance:
            logger.error(f'Not enough ETH on wallet {self.address_wallet}')
            return
        ###

        ### Approving USDC
        await approve_token(
            amount=usdc_amount_wei,
            private_key=self.private_key,
            chain="ERA",
            from_token_address=usdc_address,
            spender=router_address,
            address_wallet=self.address_wallet,
            web3=self.web3
        )
        ###

        ### Calling addLiquidityETH and building transaction
        tx = router_contract.functions.addLiquidityETH(
            usdc_address,
            pool_address,
            usdc_amount_wei,
            await self.calc_slippage(usdc_amount_wei),
            # amountETHMin bounds to the extents to which WETH/token can go down #
            await self.calc_slippage(eth_amount_wei),
            # NEEDS TO BE REPLACED # # vReserveRatioBounds bounds to the extents to which vReserveB/vReserveA can go
            # (precision: 2 ** 112) #
            [2268042980914710954931048064863569251637808, 3095862508361089959503219402198881505423112],
            self.address_wallet,
            await self.get_deadline()
        ).build_transaction({
            'from': self.address_wallet,
            'value': eth_amount_wei,
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
            f'Added {usdc_amount} {usdc_symbol}, {eth_amount} {eth_symbol} tokens to liquidity pool | TX: '
            f'https://explorer.zksync.io/tx/{tx_hash}')
        ###

    async def kyber_swap_usdc_usdt(self, usdc_amount: float):
        usdc_symbol, usdt_symbol = "USDC", "USDT"
        usdc_address, usdt_address = cnf.tokens[usdc_symbol.upper()], cnf.tokens[usdt_symbol.upper()]

        pool_address = "0x5d83c0850570de35eaf5c9d6215bf2e8020f656b"
        pool_data = await Staker.kyber_swap_pool_data(pool_address)
        router_address = "0x937f4f2FF1889b79dAa08debfCA5C237a07A5208"
        router_contract = self.web3.eth.contract(address=Web3.to_checksum_address(router_address),
                                                 abi=await load_abi('kyber_swap_router'))

        ### Verifying USDC balance
        usdc_amount_wei, usdc_contract = await create_amount(usdc_symbol, self.web3, usdc_address, usdc_amount)
        usdc_amount_wei = int(usdc_amount_wei)
        usdc_balance = await get_wallet_balance(usdc_symbol, self.web3, self.address_wallet, usdc_contract, 'ERA')
        if usdc_amount_wei > usdc_balance:
            logger.error(f'Not enough USDC on wallet {self.address_wallet}')
            return
        ###

        ### Verifying USDT balance
        usdt_amount = await Staker.get_relative_amount(pool_data, usdc_amount)
        usdt_amount_wei, usdt_contract = await create_amount(usdt_symbol, self.web3, usdt_address, usdt_amount)
        usdt_amount_wei = int(usdt_amount_wei)
        usdt_balance = await get_wallet_balance(usdt_symbol, self.web3, self.address_wallet, usdt_contract, "ERA")
        if usdt_amount_wei > usdt_balance:
            logger.error(f'Not enough USDT on wallet {self.address_wallet}')
            return
        ###

        ### Approving USDC
        await approve_token(
            amount=usdc_amount_wei,
            private_key=self.private_key,
            chain="ERA",
            from_token_address=usdc_address,
            spender=router_address,
            address_wallet=self.address_wallet,
            web3=self.web3
        )
        ###

        ### Approving USDT
        await approve_token(
            amount=usdt_amount_wei,
            private_key=self.private_key,
            chain="ERA",
            from_token_address=usdt_address,
            spender=router_address,
            address_wallet=self.address_wallet,
            web3=self.web3
        )
        ###

        ### Calling addLiquidity and building transaction
        tx = router_contract.functions.addLiquidity(
            usdc_address,
            usdt_address,
            Web3.to_checksum_address(pool_address),
            usdc_amount_wei,
            usdt_amount_wei,
            await self.calc_slippage(usdc_amount_wei),
            await self.calc_slippage(usdt_amount_wei),
            # NEEDS TO BE REPLACED # # vReserveRatioBounds bounds to the extents to which vReserveB/vReserveA can go
            [5101513987781103730432429426759680, 5273388500221114823200594546626610],
            self.address_wallet,
            await self.get_deadline()
        ).build_transaction({
            'from': self.address_wallet,
            'value': 0,
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
            f'Added {usdc_amount} {usdc_symbol}, {usdt_amount} {usdt_symbol} tokens to liquidity pool | TX: '
            f'https://explorer.zksync.io/tx/{tx_hash}')
        ###


async def main():
    staker = Staker(cnf.test_private_key)
    await staker.kyber_swap_usdc_usdt(0.3)

if __name__ == "__main__":
    asyncio.run(main())
