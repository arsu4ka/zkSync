import asyncio

import web3
from web3 import Web3

from modules import Depositor, Swapper, Staker, MintBridge

import config as cnf
from utils import get_wallet_balance, load_contract


class MainRoute:
    def __init__(self, private_key, address):
        self.private_key = private_key
        self.address = address
        self.web3_zksync_node = Web3(web3.HTTPProvider(cnf.node))
        self.swapper = Swapper(self.private_key, cnf.node, cnf.chain, 3, 1000000, cnf.tokens)

    async def deposit(self, amount_usdc, amount_eth):
        depositor = Depositor(self.private_key)
        web3_zksync_node = Web3(web3.HTTPProvider(cnf.node))
        zksync_usdc_contract = await load_contract(cnf.tokens['USDC'], web3_zksync_node, 'erc20')
        zksync_eth_contract = await load_contract(cnf.tokens['ETH'], web3_zksync_node, 'erc20')
        zksync_usdc_balance = await get_wallet_balance('usdc', web3_zksync_node, self.address, zksync_usdc_contract,
                                                       'zksync')
        await depositor.deposit_arbitrum_usdc_to_zksync(amount_usdc, cnf.orbiter_arbi_to_zk_usdc_fee, cnf.orbiter_zksync_suffix,
                                                        cnf.arbitrum_node, cnf.atbitrum_ca, 'erc20')
        while zksync_usdc_balance == await get_wallet_balance('usdc', web3_zksync_node, self.address,
                                                              zksync_usdc_contract, 'zksync'):
            print('Waiting for deposit of usdc 10 more seconds')
            await asyncio.sleep(10)

    async def staking(self):
        staker = Staker(self.private_key, cnf.node, 3, 1000000, cnf.tokens)
        await staker.sync_swap('0x80115c708E12eDd42E504c1cD52Aea96C547c05c', cnf.sync_swap_router_address,
                               'sync_swap_router', 1, 0.0003)
        await asyncio.sleep(10)
        await staker.kyber_swap(cnf.kyber_swap_router_address, 'kyber_swap_router',
                                '0x5d83c0850570de35eaf5c9d6215bf2e8020f656b', 1)

    async def swap_eth_to_usdc_mute(self):
        zksync_usdc_contract = await load_contract(cnf.tokens['USDC'], self.web3_zksync_node, 'erc20')
        zksync_usdc_balance = await get_wallet_balance('usdc', self.web3_zksync_node, self.address, zksync_usdc_contract,
                                                       'zksync')

        await self.swapper.mute_swap(0.001, 'ETH', 'USDC', cnf.mute_contract_address, 'mute')
        while zksync_usdc_balance == await get_wallet_balance('usdc', self.web3_zksync_node, self.address,
                                                              zksync_usdc_contract, 'zksync'):
            print('Waiting 10 secs swap mute')
            await asyncio.sleep(10)

    async def swap_usdc_to_mute_inch(self):
        zksync_usdc_contract = await load_contract(cnf.tokens['USDC'], self.web3_zksync_node, 'erc20')
        zksync_usdc_balance = await get_wallet_balance('usdc', self.web3_zksync_node, self.address,
                                                       zksync_usdc_contract, 'zksync')
        zksync_usdc_balance /= 1000000
        while True:
            try:
                await self.swapper.inch_swap(zksync_usdc_balance, 'USDC', 'MUTE', cnf.inch_api_url_base)
                break
            except Exception as e:
                print(e)
                pass
        while zksync_usdc_balance == await get_wallet_balance('mute', self.web3_zksync_node, self.address,
                                                               zksync_usdc_contract, 'zksync') / 1000000:
            print('Waiting, 1inch swap')
            await asyncio.sleep(10)

    async def swap_mute_to_eth_sync_swap(self):
        zksync_mute_contract = await load_contract(cnf.tokens['MUTE'], self.web3_zksync_node, 'erc20')
        zksync_mute_balance = await get_wallet_balance('mute', self.web3_zksync_node, self.address,
                                                       zksync_mute_contract, 'zksync')
        zksync_mute_balance /= 10 ** 18
        zksync_eth_contract = await load_contract(cnf.tokens['ETH'], self.web3_zksync_node, 'erc20')
        zksync_eth_balance = await get_wallet_balance('eth', self.web3_zksync_node, self.address,
                                                      zksync_eth_contract, 'zksync')
        await self.swapper.sync_swap(zksync_mute_balance, 'MUTE', 'ETH')
        while zksync_eth_balance == await get_wallet_balance('eth', self.web3_zksync_node, self.address,
                                                             zksync_eth_contract, 'zksync'):
            print('Waiting 10 secs swap sync')
            await asyncio.sleep(10)

    async def swaps(self):
        await self.swap_eth_to_usdc_mute()
        await asyncio.sleep(10)
        await self.swap_usdc_to_mute_inch()
        await asyncio.sleep(10)
        await self.swap_mute_to_eth_sync_swap()

    async def extras(self):
        minter = MintBridge(self.private_key, 'Arbitrum', cnf.node, cnf.mint_contract_address, 'mint_and_bridge')
        await minter.mint()


    async def start(self):
        # await self.deposit(1, 0.0001)
        # await asyncio.sleep(10)
        await self.swaps()
        # await asyncio.sleep(30)
        # await self.staking()
        # await asyncio.sleep(10)
        # await self.extras()


async def main():
    wallets = [['', '']]

    tasks = []

    for private_key, wallet in wallets:
        tasks.append(asyncio.create_task(MainRoute(private_key, wallet).start()))

    for task in tasks:
        await task


if __name__ == '__main__':
    asyncio.run(main())
