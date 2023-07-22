import asyncio

from loguru import logger

from web3 import Web3

from modules import Depositor, Swapper, Staker, MintBridge

import utils
import config as cnf
from balance_decorator import BalanceCheckerDecorator


class Runner:
    def __init__(self, private_key: str, tier: str):
        self.cycles = 0
        self.tier = tier.lower()
        if tier.lower() == "diamond":
            self.cycles = 109
        elif tier.lower() == "1":
            self.cycles = 40
        elif tier.lower() == "2":
            self.cycles = 20

        self.web3_zksync = Web3(Web3.HTTPProvider(cnf.node))
        self.private_key = private_key
        self.address = utils.get_wallet_address_from_private_key(self.web3_zksync, private_key)
        self.swapper = Swapper(
            self.private_key,
            cnf.node,
            cnf.chain,
            cnf.slippage_percent,
            cnf.deadline_minutes,
            cnf.tokens
        )
        self.staker = Staker(
            self.private_key,
            cnf.node,
            1,
            cnf.deadline_minutes,
            cnf.tokens
        )
        self.depositor = Depositor(self.private_key)

    @property
    async def nonce(self):
        return self.web3_zksync.eth.get_transaction_count(Web3.to_checksum_address(self.address))

    @property
    async def eth_balance(self):
        eth_balance_wei = self.web3_zksync.eth.get_balance(Web3.to_checksum_address(self.address))
        eth_balance_float = await utils.wei_to_amount(self.web3_zksync, eth_balance_wei, cnf.tokens["ETH"])
        return eth_balance_float

    @property
    async def usdc_balance(self):
        balance = await utils.get_wallet_balance(self.web3_zksync, self.address, cnf.tokens["USDC"])
        return balance

    @property
    async def usdt_balance(self):
        balance = await utils.get_wallet_balance(self.web3_zksync, self.address, cnf.tokens["USDT"])
        return balance

    async def perform_swap_eth_to_usdc(self, amount_to_swap: float = None):
        @BalanceCheckerDecorator(self, cnf.tokens["USDC"])
        async def swap_eth_to_usdc_mute(eth_amount_to_swap: float):
            await self.swapper.mute_swap(eth_amount_to_swap, 'ETH', 'USDC', cnf.mute_contract_address, 'mute')

        amount = amount_to_swap if amount_to_swap else await self.eth_balance * 0.9
        return await swap_eth_to_usdc_mute(amount)

    async def perform_swaps(self):
        @BalanceCheckerDecorator(self, cnf.tokens["USDC"])
        async def swap_usdt_to_usdc_inch(usdt_amount_to_swap: float):
            await self.swapper.inch_swap(usdt_amount_to_swap, "USDT", "USDC", cnf.inch_api_url_base)

        @BalanceCheckerDecorator(self, cnf.tokens["USDT"])
        async def swap_usdc_to_usdt_inch(usdc_amount_to_swap: float):
            await self.swapper.inch_swap(usdc_amount_to_swap, "USDC", "USDT", cnf.inch_api_url_base)

        async def swap_usdc_to_myself():
            await self.swapper.transfer_to_sender_wallet(cnf.tokens["USDC"])

        async def swap_usdt_to_myself():
            await self.swapper.transfer_to_sender_wallet(cnf.tokens["USDT"])

        @BalanceCheckerDecorator(self, cnf.tokens["ETH"])
        async def stake_eth():
            await self.staker.sync_swap(
                cnf.sync_swap_usdc_eth_pool,
                cnf.sync_swap_router_address,
                "sync_swap_router",
                0,
                0.00037
            )

        while await self.nonce < self.cycles:
            if self.tier == "diamond":
                result = await stake_eth()
                if result is None:
                    exit()

            if await self.usdc_balance > await self.usdt_balance:
                await swap_usdc_to_myself()
            await asyncio.sleep(10)

            if await self.usdc_balance > await self.usdt_balance:
                result = await swap_usdc_to_usdt_inch(await self.usdc_balance)
                if result is None:
                    exit()

            if await self.usdt_balance > await self.usdc_balance:
                await swap_usdt_to_myself()
            await asyncio.sleep(10)

            if await self.usdt_balance > await self.usdc_balance:
                result = await swap_usdt_to_usdc_inch(await self.usdt_balance)
                if result is None:
                    exit()

            if await self.usdc_balance > await self.usdt_balance:
                await swap_usdc_to_myself()
            await asyncio.sleep(10)

    async def perform_extras(self):
        @BalanceCheckerDecorator(self, cnf.tokens["ETH"])
        async def mint_and_bridge():
            minter = MintBridge(self.private_key, 'Arbitrum', cnf.node, cnf.mint_contract_address, 'mint_and_bridge')
            await minter.mint()

        @BalanceCheckerDecorator(self, cnf.tokens["USDC"])
        async def swap_usdt_to_usdc_inch(usdt_amount_to_swap: float):
            await self.swapper.inch_swap(usdt_amount_to_swap, "USDT", "USDC", cnf.inch_api_url_base)

        async def withdraw():
            if await self.usdt_balance > await self.usdc_balance:
                result = await swap_usdt_to_usdc_inch(await self.usdt_balance)
                if result is None:
                    exit()

            if await self.eth_balance - 0.0015 >= 0:
                result = await self.perform_swap_eth_to_usdc(await self.eth_balance - 0.0015)
                if result is None:
                    exit()

            usdc_amount = await self.usdc_balance - (cnf.orbiter_arbi_to_zk_usdc_fee + 0.1)
            await self.depositor.deposit_zkcync_usdc_to_arbitrum(
                usdc_amount,
                cnf.orbiter_arbi_to_zk_usdc_fee,
                cnf.orbiter_arbitrum_suffix,
                cnf.node,
                cnf.tokens["USDC"]
            )

        status = await mint_and_bridge()
        if status is None:
            exit()
        await asyncio.sleep(10)
        await withdraw()


async def main():
    wallets = cnf.private_key_list

    main_route_list = [Runner(pk, "diamond") for pk in wallets]

    tasks = []
    for main_route in main_route_list:
        # task = asyncio.create_task(main_route.perform_swaps())
        task = asyncio.create_task(main_route.perform_extras())
        tasks.append(task)

    for task in tasks:
        await task


if __name__ == '__main__':
    asyncio.run(main())
