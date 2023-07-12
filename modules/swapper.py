from aiohttp import ClientSession
from loguru import logger
from web3 import Web3
from eth_abi import encode
import json
import swapper_config as cnf
import asyncio

from helper import (
    approve_token,
    setup_transaction_data,
    setup_tokens_addresses,
    load_abi,
    create_amount,
    get_wallet_balance
)


class Swapper:
    def __init__(self,
                 private_key: str,
                 rpc_chain: str = cnf.node,
                 chain: dict[str, str] = cnf.chain,
                 mute_api_url: str = cnf.mute_api_url_base,
                 slippage: int = cnf.slippage_percent,
                 deadline: int = cnf.deadline_minutes
                 ) -> None:
        self.api_url = mute_api_url
        self.web3 = Web3(Web3.HTTPProvider(rpc_chain))
        self.private_key = private_key
        self.chain, self.chain_id = chain["name"], chain["id"]
        self.slippage = slippage
        self.deadline_minutes = deadline
        self.account = self.web3.eth.account.from_key(private_key)
        self.address_wallet = self.account.address
        self.nonce = self.web3.eth.get_transaction_count(self.address_wallet)

    async def get_deadline(self) -> int:
        import datetime, time
        return int(time.time() + datetime.timedelta(minutes=self.deadline_minutes).total_seconds())

    async def send_requests(self, url: str, params=None) -> json:
        if params is None:
            params = {}
        async with ClientSession() as session:
            response = await session.get(url, params=params)
            response_text = await response.json()
        return response_text
    
    async def mute_swap(self, amount: float, from_token_symbol: str = "ETH", to_token_symbol: str = "USDC") -> None:
        mute_contract_address = "0x8B791913eB07C32779a16750e3868aA8495F5964"
        to_token_address, from_token_address = await setup_tokens_addresses(from_token=from_token_symbol,
                                                                            to_token=to_token_symbol)
        mute_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(mute_contract_address),
            abi=await load_abi('mute'))

        value, token_contract = await create_amount(from_token_symbol, self.web3, from_token_address, amount)
        value = int(value)

        balance = await get_wallet_balance(from_token_symbol, self.web3, self.address_wallet, token_contract, 'ERA')

        if value > balance:
            raise Exception(f'Not enough balance for wallet {self.address_wallet}')

        if from_token_symbol.lower() != 'eth':
            await approve_token(amount=value,
                                private_key=self.private_key,
                                chain='ERA',
                                from_token_address=from_token_address,
                                spender=mute_contract_address,
                                address_wallet=self.address_wallet,
                                web3=self.web3)

        if from_token_symbol.lower() == 'eth':
            tx = mute_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,
                [Web3.to_checksum_address(from_token_address), Web3.to_checksum_address(to_token_address)],
                self.address_wallet,
                await self.get_deadline(),
                [False, False]
            ).build_transaction({
                'value': value,
                'nonce': self.web3.eth.get_transaction_count(self.address_wallet),
                'from': self.address_wallet,
                'maxFeePerGas': 0,
                'maxPriorityFeePerGas': 0,
                'gas': 0
            })
        else:
            tx = mute_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                value,
                0,
                [Web3.to_checksum_address(from_token_address), Web3.to_checksum_address(to_token_address)],
                self.address_wallet,
                await self.get_deadline(),
                [False, False]
            ).build_transaction({
                'value': 0,
                'nonce': self.web3.eth.get_transaction_count(self.address_wallet),
                'from': self.address_wallet,
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
            f'Swapped {amount} {from_token_symbol} tokens => {to_token_symbol} | TX: https://explorer.zksync.io/tx/{tx_hash}')

    async def inch_swap(
                        self, 
                        amount: float, 
                        from_token_address: str = cnf.tokens["USDC"],
                        to_token_address: str = cnf.tokens["MUTE"]
                        ) -> None:
        response = await self.send_requests(url=f'{self.api_url}/approve/spender')
        spender = response['address']
        from_token_address, from_decimals, to_token_address = await setup_transaction_data(self.web3,
                                                                                           from_token_address,
                                                                                           to_token_address)
        amount = int(amount * 10 ** from_decimals)

        if from_token_address != '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE':
            await approve_token(amount, self.private_key, self.chain, from_token_address, spender, self.address_wallet,
                                self.web3)

        response = await self.send_requests(
            url=f'{self.api_url}/swap',
            params={
                "fromTokenAddress": from_token_address,
                "toTokenAddress": to_token_address,
                "amount": amount,
                "fromAddress": self.address_wallet,
                "slippage": self.slippage 
            })
        from_token = response['fromToken']['symbol']
        to_token = response['toToken']['symbol']
        to_token_decimals = response['toToken']['decimals']
        to_token_amount = float(response['toTokenAmount']) / 10 ** to_token_decimals
        tx = response['tx']
        tx['chainId'] = self.chain_id
        tx['nonce'] = self.nonce
        tx['to'] = Web3.to_checksum_address(tx['to'])
        tx['gasPrice'] = int(tx['gasPrice'])
        tx['gas'] = int(int(tx['gas']))
        tx['value'] = int(tx['value'])

        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)

        logger.success(
            f'Swapped {amount / 10 ** from_decimals} {from_token} tokens => to {to_token_amount} {to_token} | Tx hash: {tx_hash}')

    async def syncswap(self, amount: float, from_token_symbol: str = "MUTE", to_token_symbol: str = "ETH") -> None:
        router_address = "0x2da10A1e27bF85cEdD8FFb1AbBe97e53391C0295"
        classic_pool_factory_address = "0xf2DAd89f2788a8CD54625C60b55cD3d2D0ACa7Cb"

        to_token_address, from_token_address = await setup_tokens_addresses(from_token=from_token_symbol,
                                                                            to_token=to_token_symbol)

        classic_pool_factory = self.web3.eth.contract(
            address=Web3.to_checksum_address(classic_pool_factory_address),
            abi=await load_abi('classic_pool_factory_address'))
        pool_address = classic_pool_factory.functions.getPool(Web3.to_checksum_address(from_token_address),
                                                              Web3.to_checksum_address(to_token_address)).call()

        value, token_contract = await create_amount(from_token_symbol, self.web3, from_token_address, amount)
        value = int(value)
        balance = await get_wallet_balance(from_token_symbol, self.web3, self.address_wallet, token_contract, 'ERA')

        if value > balance:
            logger.error(f'Not enough money for wallet {self.address_wallet}')
            return

        if pool_address == "0x0000000000000000000000000000000000000000":
            logger.error(f'There is no pool')
            return

        swap_data = encode(
            ["address", "address", "uint8"],
            [Web3.to_checksum_address(from_token_address), self.address_wallet, 1]
        )
        native_eth_address = "0x0000000000000000000000000000000000000000"

        steps = [{
            "pool": pool_address,
            "data": swap_data,
            "callback": native_eth_address,
            "callbackData": '0x'
        }]

        paths = [{
            "steps": steps,
            "tokenIn": Web3.to_checksum_address(
                from_token_address) if from_token_symbol.lower() != 'eth' else Web3.to_checksum_address(
                native_eth_address),
            "amountIn": value,
        }]

        router = self.web3.eth.contract(address=Web3.to_checksum_address(router_address),
                                        abi=await load_abi('sync_swap_router'))

        if from_token_symbol.lower() != 'eth':
            await approve_token(amount=value,
                                private_key=self.private_key,
                                chain='ERA',
                                from_token_address=from_token_address,
                                spender=router_address,
                                address_wallet=self.address_wallet,
                                web3=self.web3)

        tx = router.functions.swap(
            paths,
            0,
            await self.get_deadline()
        ).build_transaction({
            'from': self.address_wallet,
            'value': value if from_token_symbol.lower() == 'eth' else 0,
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
            f'Swapped {amount} {from_token_symbol} tokens => {to_token_symbol} | TX: https://explorer.zksync.io/tx/{tx_hash}')


async def test_run():
    swapper = Swapper(
        private_key=cnf.test_private_key
    )
    await swapper.syncswap(1.0)
    
if __name__ == "__main__":
    asyncio.run(test_run())
