import asyncio
import traceback

from loguru import logger

import web3
from web3 import Web3

from modules import Depositor, Swapper, Staker, MintBridge

import utils
import config as cnf


class BalanceCheckerDecorator:
    def __init__(self, token_ca: str):
        self.token_ca = token_ca

    @staticmethod
    def traceback_to_file(exc: str, wallet: str):
        with open("errors.txt", "a") as f:
            f.write(f"\n{exc}\n{wallet}\n")

    def __call__(self, async_function):
        async def execute_and_return(obj, amount_to_swap):
            if self.token_ca == "0x000000000000000000000000000000000000800A":
                async def balance() -> float:
                    amount_wei = obj.web3_zksync_node.eth.get_balance(Web3.to_checksum_address(obj.address))
                    return await utils.wei_to_amount(obj.web3_zksync_node, amount_wei, cnf.tokens["ETH"])
            else:
                async def balance() -> float:
                    return await utils.get_wallet_balance(obj.web3_zksync_node, obj.address, self.token_ca)

            bal = await balance()
            await async_function(obj, amount_to_swap)
            i = 1
            while bal == await balance():
                if i > 10:
                    BalanceCheckerDecorator.traceback_to_file("Exceeded max waiting time", obj.address)
                    return None
                logger.info("Sleeping 5 seconds, until balance updates")
                await asyncio.sleep(5)
                i += 1
            logger.success("Balance updated")
            return await balance()

        return execute_and_return


class MainRoute:
    def __init__(self, private_key: str, cycles: int):
        self.web3_zksync_node = Web3(web3.HTTPProvider(cnf.node))
        self.private_key = private_key
        self.address = utils.get_wallet_address_from_private_key(self.web3_zksync_node, private_key)
        self.cycles = cycles
        self.swapper = Swapper(self.private_key, cnf.node, cnf.chain, 0.5, 1000000, cnf.tokens)
        self.depositor = Depositor(self.private_key)

    async def transfer_usdc_to_sender_wallet(self):
        nonce = self.web3_zksync_node.eth.get_transaction_count(Web3.to_checksum_address(self.address))
        usdc_ca = cnf.tokens["USDC"]
        usdc_contract = await utils.get_token_contract(self.web3_zksync_node, usdc_ca)
        usdc_balance = await utils.get_wallet_balance(self.web3_zksync_node, self.address, usdc_ca)
        usdc_balance_wei = await utils.amount_to_wei(self.web3_zksync_node, usdc_balance, usdc_ca)
        tx = usdc_contract.functions.transfer(*(
            Web3.to_checksum_address(self.address),
            usdc_balance_wei
        )).build_transaction({
            'chainId': self.swapper.chain_id,
            'from': self.address,
            'nonce': nonce,
            'maxFeePerGas': 0,
            'maxPriorityFeePerGas': 0,
            'gas': 0
        })

        tx.update({'maxFeePerGas': self.web3_zksync_node.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3_zksync_node.eth.gas_price})
        tx.update({'gas': self.web3_zksync_node.eth.estimate_gas(tx)})

        signed_tx = self.web3_zksync_node.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3_zksync_node.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3_zksync_node.to_hex(raw_tx_hash)
        logger.success(
            f'Transferred to itself {usdc_balance} USDC tokens | '
            f'{self.address} | '
            f'TX: https://explorer.zksync.io/tx/{tx_hash}'
        )

    @BalanceCheckerDecorator(cnf.tokens["USDC"])
    async def deposit(self, amount_to_swap):
        web3_arbitrum = Web3(Web3.HTTPProvider(cnf.arbitrum_node))
        usdc_balance = await utils.get_wallet_balance(web3_arbitrum, self.address, cnf.arbitrum_ca)
        usdc_balance -= (cnf.orbiter_arbi_to_zk_usdc_fee + 0.1)
        await self.depositor.deposit_arbitrum_usdc_to_zksync(
            usdc_balance,
            cnf.orbiter_arbi_to_zk_usdc_fee,
            cnf.orbiter_zksync_suffix,
            cnf.arbitrum_node,
            cnf.arbitrum_ca,
            'erc20'
        )

    async def staking(self, eth_to_stake: float):
        staker = Staker(self.private_key, cnf.node, 3, 1000000, cnf.tokens)
        # usdt_balance = await self.swap_eth_to_usdt_sync_swap(0.0005)
        # usdc_balance = await self.swap_eth_to_usdc_mute(0.0005)
        await staker.sync_swap('0x80115c708E12eDd42E504c1cD52Aea96C547c05c', cnf.sync_swap_router_address,
                               'sync_swap_router', 0.1, eth_to_stake)
        # await asyncio.sleep(10)
        # await staker.kyber_swap(cnf.kyber_swap_router_address, 'kyber_swap_router',
        #                         '0x5d83c0850570de35eaf5c9d6215bf2e8020f656b', usdc_balance)

    @BalanceCheckerDecorator(cnf.tokens["USDC"])
    async def swap_eth_to_usdc_mute(self, eth_amount_to_swap: float):
        await self.swapper.mute_swap(eth_amount_to_swap, 'ETH', 'USDC', cnf.mute_contract_address, 'mute')

    @BalanceCheckerDecorator(cnf.tokens["USDT"])
    async def swap_usdc_to_usdt_inch(self, usdc_amount_to_swap: float):
        nonce = self.web3_zksync_node.eth.get_transaction_count(self.web3_zksync_node.to_checksum_address(self.address))
        while True:
            try:
                await self.swapper.inch_swap(usdc_amount_to_swap, 'USDC', 'USDT', cnf.inch_api_url_base, nonce)
                break
            except Exception as e:
                logger.error(e)
                BalanceCheckerDecorator.traceback_to_file(traceback.format_exc(), self.address)
                nonce += 1
                pass

    @BalanceCheckerDecorator(cnf.tokens["USDC"])
    async def swap_usdt_to_usdc_inch(self, usdt_amount_to_swap: float):
        nonce = self.web3_zksync_node.eth.get_transaction_count(self.web3_zksync_node.to_checksum_address(self.address))
        while True:
            try:
                await self.swapper.inch_swap(usdt_amount_to_swap, 'USDT', 'USDC', cnf.inch_api_url_base, nonce)
                break
            except Exception as e:
                logger.error(e)
                BalanceCheckerDecorator.traceback_to_file(traceback.format_exc(), self.address)
                nonce += 1
                pass

    @BalanceCheckerDecorator(cnf.tokens["USDC"])
    async def swap_usdt_to_usdc_sync_swap(self, mute_amount_to_swap: float):
        await self.swapper.sync_swap(mute_amount_to_swap, 'USDT', 'USDC')

    @BalanceCheckerDecorator(cnf.tokens["USDT"])
    async def swap_eth_to_usdt_sync_swap(self, eth_amount_to_swap: float):
        await self.swapper.sync_swap(eth_amount_to_swap, "ETH", "USDT")

    async def swaps(self, usdc_balance: float):
        await self.transfer_usdc_to_sender_wallet()
        await asyncio.sleep(30)
        # await self.staking(0.00037)
        await self.transfer_usdc_to_sender_wallet()
        await asyncio.sleep(30)
        usdt_balance = await self.swap_usdc_to_usdt_inch(usdc_balance)
        # await self.swap_usdt_to_usdc_sync_swap(usdt_balance)
        await self.swap_usdt_to_usdc_inch(usdt_balance)
        await self.transfer_usdc_to_sender_wallet()
        await asyncio.sleep(30)
        usdc_balance = await utils.get_wallet_balance(self.web3_zksync_node, self.address, cnf.tokens["USDC"])
        return usdc_balance

    async def extras(self):
        minter = MintBridge(self.private_key, 'Arbitrum', cnf.node, cnf.mint_contract_address, 'mint_and_bridge')
        await minter.mint()

    async def withdraw(self):
        eth_balance_wei = self.web3_zksync_node.eth.get_balance(Web3.to_checksum_address(self.address))
        eth_balance = await utils.wei_to_amount(self.web3_zksync_node, eth_balance_wei, cnf.tokens["ETH"])
        eth_balance -= 0.0015
        usdc_balance = await self.swap_eth_to_usdc_mute(eth_balance)
        usdc_balance -= (cnf.orbiter_arbi_to_zk_usdc_fee + 0.1)
        await self.depositor.deposit_zkcync_usdc_to_arbitrum(
            usdc_balance,
            cnf.orbiter_arbi_to_zk_usdc_fee,
            cnf.orbiter_arbitrum_suffix,
            cnf.node,
            cnf.tokens["USDC"]
        )

    async def start_diamond(self):
        try:
            # await self.deposit(None)
            # await asyncio.sleep(10)
            # eth_balance_wei = self.web3_zksync_node.eth.get_balance(Web3.to_checksum_address(self.address))
            # eth_balance = await utils.wei_to_amount(self.web3_zksync_node, eth_balance_wei, cnf.tokens["ETH"])
            # eth_balance *= 0.9
            # usdc_balance = await self.swap_eth_to_usdc_mute(eth_balance)
            usdc_balance = await utils.get_wallet_balance(self.web3_zksync_node, self.address, cnf.tokens["USDC"])
            usdt_balance = await utils.get_wallet_balance(self.web3_zksync_node, self.address, cnf.tokens["USDT"])
            if usdt_balance > usdc_balance:
                await self.swap_usdt_to_usdc_inch(usdt_balance)

            for _ in range(self.cycles):
                usdc_balance = await self.swaps(usdc_balance)

            # await self.staking(0.005)
            # await asyncio.sleep(10)
            # await self.extras()
            # await asyncio.sleep(10)
            # await self.withdraw()
        except Exception as e:
            logger.error(
                f"Process failed with error: {e}. "
                f"Wallet public address: {self.address}, private key: {self.private_key}"
            )

    async def start(self):
        # await self.deposit(None)
        # await asyncio.sleep(10)

        for _ in range(self.cycles):
            await self.swaps()
            await asyncio.sleep(10)

        await self.staking()
        await asyncio.sleep(10)
        await self.extras()
        await asyncio.sleep(10)
        await self.withdraw()
        await asyncio.sleep(2)
        print(f"{self.address}")


async def main():
    wallets = cnf.private_key_list

    main_route_list = [MainRoute(pk, 3) for pk in wallets]

    tasks = []
    for main_route in main_route_list:
        # usdt_balance = await utils.get_wallet_balance(main_route.web3_zksync_node, main_route.address, cnf.tokens["USDT"])
        # task = asyncio.create_task(main_route.swap_usdt_to_usdc_sync_swap(usdt_balance))
        task = asyncio.create_task(main_route.start_diamond())
        tasks.append(task)

    for task in tasks:
        await task


if __name__ == '__main__':
    asyncio.run(main())
