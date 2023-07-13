from aiohttp import ClientSession
from loguru import logger
from web3 import Web3
from eth_abi import encode
import json

from utils.helper import (
    approve_token,
    setup_transaction_data,
    setup_tokens_addresses,
    load_abi,
    create_amount,
    get_wallet_balance,
    load_contract
)


class Swapper:
    def __init__(self,
                 private_key: str,
                 rpc_chain: str,
                 chain: dict[str, str],
                 slippage: int,
                 deadline: int,
                 tokens: dict[str, str]
                 ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_chain))
        self.private_key = private_key
        self.chain, self.chain_id = chain["name"], chain["id"]
        self.slippage = slippage
        self.deadline_minutes = deadline
        self.account = self.web3.eth.account.from_key(private_key)
        self.address_wallet = self.account.address
        self.nonce = self.web3.eth.get_transaction_count(self.address_wallet)
        self.tokens = tokens

    async def get_deadline(self) -> int:
        import datetime
        import time
        return int(time.time() + datetime.timedelta(minutes=self.deadline_minutes).total_seconds())

    @staticmethod
    async def send_requests(url: str, params=None) -> json:
        if params is None:
            params = {}
        async with ClientSession() as session:
            response = await session.get(url, params=params)
            response_text = await response.json()
        return response_text

    async def mute_swap(
            self,
            amount: float,
            from_token_symbol: str,
            to_token_symbol: str,
            mute_contract_address: str,
            mute_abi_name: str
    ) -> None:
        from_token_symbol = from_token_symbol.upper()
        to_token_symbol = to_token_symbol.upper()
        from_token_address, to_token_address = await setup_tokens_addresses(
            token1_symbol=from_token_symbol,
            token2_symbol=to_token_symbol,
            tokens=self.tokens
        )
        mute_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(mute_contract_address),
            abi=await load_abi(mute_abi_name))

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
                                from_token_symbol=from_token_symbol,
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
        elif to_token_symbol.lower() == "eth":
            tx = mute_contract.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                value,
                0, ### fix
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
        else:
            tx = mute_contract.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
                value,
                1,
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
            f'Swapped {amount} {from_token_symbol} tokens => {to_token_symbol} | '
            f'TX: https://explorer.zksync.io/tx/{tx_hash}')

    async def inch_swap(
                        self,
                        amount: float,
                        from_token_symbol: str,
                        to_token_symbol: str,
                        api_url: str
                        ) -> None:
        from_token_symbol = from_token_symbol.upper()
        to_token_symbol = to_token_symbol.upper()
        from_token_address, to_token_address = await setup_tokens_addresses(
            token1_symbol=from_token_symbol,
            token2_symbol=to_token_symbol,
            tokens=self.tokens
        )

        response = await Swapper.send_requests(url=f'{api_url}/approve/spender')
        spender = response['address']
        from_token_address, from_decimals, to_token_address = await setup_transaction_data(self.web3,
                                                                                           from_token_address,
                                                                                           to_token_address)
        amount = int(amount * 10 ** from_decimals)

        if from_token_address != '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE':
            await approve_token(
                amount,
                self.private_key,
                self.chain,
                from_token_address,
                from_token_symbol,
                spender,
                self.address_wallet,
                self.web3
            )

        response = await Swapper.send_requests(
            url=f'{api_url}/swap',
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
            f'Swapped {amount / 10 ** from_decimals} {from_token_symbol} tokens => '
            f'to {to_token_amount} {to_token_symbol} |'
            f'Tx hash: {tx_hash}')

    async def sync_swap(
            self,
            amount: float,
            from_token_symbol: str,
            to_token_symbol: str,
            router_address: str = "0x2da10A1e27bF85cEdD8FFb1AbBe97e53391C0295",
            router_abi: str = "sync_swap_router",
            classic_pool_factory_address: str = "0xf2DAd89f2788a8CD54625C60b55cD3d2D0ACa7Cb",
            classic_pool_factory_abi: str = "classic_pool_factory_address"
    ) -> None:

        from_token_symbol = from_token_symbol.upper()
        to_token_symbol = to_token_symbol.upper()
        from_token_address, to_token_address = await setup_tokens_addresses(
            token1_symbol=from_token_symbol,
            token2_symbol=to_token_symbol,
            tokens=self.tokens
        )

        classic_pool_factory = await load_contract(
            classic_pool_factory_address,
            self.web3,
            classic_pool_factory_abi
        )
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

        router = await load_contract(router_address, self.web3, router_abi)
        if from_token_symbol.lower() != 'eth':
            await approve_token(amount=value,
                                private_key=self.private_key,
                                chain='ERA',
                                from_token_address=from_token_address,
                                from_token_symbol=from_token_symbol,
                                spender=router_address,
                                address_wallet=self.address_wallet,
                                web3=self.web3)

        tx = router.functions.swap(
            paths,
            Web3.to_wei(0.45, "ether") // 1000000000000, ### fix
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
            f'Swapped {amount} {from_token_symbol} tokens => {to_token_symbol} | '
            f'TX: https://explorer.zksync.io/tx/{tx_hash}')
