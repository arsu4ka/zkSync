import web3
import asyncio
from aiohttp import ClientSession
from loguru import logger
from web3 import Web3
from eth_abi import encode
import json
import utils


class Swapper:
    def __init__(self,
                 private_key: str,
                 rpc_chain: str,
                 chain: dict[str, str],
                 slippage: float,
                 deadline: int,
                 tokens: dict[str, str]
                 ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_chain))
        self.private_key = private_key
        self.chain, self.chain_id = chain["name"], chain["id"]
        self.slippage = slippage
        self.deadline_minutes = deadline
        self.address_wallet = utils.get_wallet_address_from_private_key(self.web3, private_key)
        self.nonce = self.web3.eth.get_transaction_count(self.web3.to_checksum_address(self.address_wallet))
        self.tokens = tokens

    async def get_deadline(self) -> int:
        import datetime
        import time
        return int(time.time() + datetime.timedelta(minutes=self.deadline_minutes).total_seconds())

    async def calc_slippage(self, value: int) -> int:
        return int(value * (1 - (self.slippage / 100)))

    async def get_amount_out_min(
            self,
            from_token_symbol: str,
            to_token_symbol: str,
            from_token_amount: float,
            to_token_address: str
    ) -> int:
        from_token_ticker = f"\"{from_token_symbol}USDT\""
        to_token_ticker = f"\"{to_token_symbol}USDT\""
        data = await Swapper.send_requests(
            "https://api.binance.com/api/v3/ticker/price",
            {"symbols": "[" + from_token_ticker + "," + to_token_ticker + "]"}
        )
        try:
            prices = {
                item["symbol"].replace("USDT", ""): float(item["price"])
                for item in data
                if "price" in item
            }
            to_token_amount = (from_token_amount * prices[from_token_symbol]) / prices[to_token_symbol]
            to_token_amount_wei = await utils.amount_to_wei(self.web3, to_token_amount, to_token_address)
            return await self.calc_slippage(int(to_token_amount_wei))
        except KeyError:
            c1 = from_token_symbol.lower() == "usdc" or from_token_symbol.lower() == "usdt"
            c2 = to_token_symbol.lower() == "usdc" or from_token_symbol.lower() == "usdt"
            if c1 and c2:
                return await self.calc_slippage(await utils.amount_to_wei(self.web3, from_token_amount, to_token_address))
            else:
                return 0

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
        from_token_address, to_token_address = await utils.setup_tokens_addresses(
            token1_symbol=from_token_symbol,
            token2_symbol=to_token_symbol,
            tokens=self.tokens
        )

        mute_contract = await utils.get_contract(mute_contract_address, self.web3, mute_abi_name)

        amount_wei = await utils.amount_to_wei(self.web3, amount, from_token_address)
        balance = await utils.get_wallet_balance(self.web3, self.address_wallet, from_token_address)

        if amount > balance:
            logger.error(
                f'Not enough {from_token_symbol} on wallet {self.address_wallet}. Want {amount}, got {balance}')
            return

        if from_token_symbol.lower() != 'eth':
            await utils.approve_token(
                                amount=amount_wei,
                                private_key=self.private_key,
                                chain='ERA',
                                from_token_address=from_token_address,
                                from_token_symbol=from_token_symbol,
                                spender=mute_contract_address,
                                web3=self.web3
            )

        amount_out_min = await self.get_amount_out_min(
                    from_token_symbol,
                    to_token_symbol,
                    amount,
                    to_token_address
                )

        tx = mute_contract.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            amount_out_min,
            [Web3.to_checksum_address(from_token_address), Web3.to_checksum_address(to_token_address)],
            self.address_wallet,
            await self.get_deadline(),
            [False, False]
        ).build_transaction({
            'value': amount_wei,
            'nonce': self.web3.eth.get_transaction_count(self.web3.to_checksum_address(self.address_wallet)),
            'from': self.address_wallet,
            'maxFeePerGas': 0,
            'maxPriorityFeePerGas': 0,
            'gas': 0
        })

        tx.update({'maxFeePerGas': self.web3.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3.eth.gas_price})
        tx.update({'gas': self.web3.eth.estimate_gas(tx)})

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
                        api_url: str,
                        ) -> None:
        from_token_symbol = from_token_symbol.upper()
        to_token_symbol = to_token_symbol.upper()
        from_token_address, to_token_address = await utils.setup_tokens_addresses(
            token1_symbol=from_token_symbol,
            token2_symbol=to_token_symbol,
            tokens=self.tokens
        )

        response = await Swapper.send_requests(url=f'{api_url}/approve/spender')
        spender = response['address']
        amount_wei = await utils.amount_to_wei(self.web3, amount, from_token_address)
        balance = await utils.get_wallet_balance(self.web3, self.address_wallet, from_token_address)

        if amount > balance:
            logger.error(f'Not enough {from_token_symbol} on wallet {self.address_wallet}. Want {amount}, got {balance}')
            return

        if from_token_symbol.lower() != 'eth':
            await utils.approve_token(
                amount_wei,
                self.private_key,
                self.chain,
                from_token_address,
                from_token_symbol,
                spender,
                self.web3
            )

        response = await Swapper.send_requests(
            url=f'{api_url}/swap',
            params={
                "fromTokenAddress": from_token_address,
                "toTokenAddress": to_token_address,
                "amount": amount_wei,
                "fromAddress": self.address_wallet,
                "slippage": self.slippage
            })

        to_token_amount = await utils.wei_to_amount(self.web3, int(response['toTokenAmount']), to_token_address)
        tx = response['tx']
        tx['chainId'] = self.chain_id
        tx['nonce'] = self.web3.eth.get_transaction_count(Web3.to_checksum_address(self.address_wallet))
        tx['to'] = Web3.to_checksum_address(tx['to'])
        tx['gasPrice'] = int(tx['gasPrice'])
        tx['gas'] = int(int(tx['gas']))
        tx['value'] = int(tx['value'])

        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)

        logger.success(
            f'Swapped {amount} {from_token_symbol} tokens => '
            f'to {to_token_amount} {to_token_symbol} | '
            f"{self.address_wallet} | "
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
        from_token_address, to_token_address = await utils.setup_tokens_addresses(
            token1_symbol=from_token_symbol,
            token2_symbol=to_token_symbol,
            tokens=self.tokens
        )

        classic_pool_factory = await utils.get_contract(
            classic_pool_factory_address,
            self.web3,
            classic_pool_factory_abi
        )
        pool_address = classic_pool_factory.functions.getPool(Web3.to_checksum_address(from_token_address),
                                                              Web3.to_checksum_address(to_token_address)).call()

        amount_wei = await utils.amount_to_wei(self.web3, amount, from_token_address)
        balance = await utils.get_wallet_balance(self.web3, self.address_wallet, from_token_address)

        if amount > balance:
            logger.error(f'Not enough {from_token_symbol} on wallet {self.address_wallet}. Want {amount}, got {balance}')
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
            "amountIn": amount_wei,
        }]

        router = await utils.get_contract(router_address, self.web3, router_abi)
        if from_token_symbol.lower() != 'eth':
            await utils.approve_token(
                                amount=amount_wei,
                                private_key=self.private_key,
                                chain='ERA',
                                from_token_address=from_token_address,
                                from_token_symbol=from_token_symbol,
                                spender=router_address,
                                web3=self.web3
            )

        tx = router.functions.swap(
            paths,
            await self.get_amount_out_min(
                from_token_symbol,
                to_token_symbol,
                amount,
                to_token_address
            ),
            await self.get_deadline()
        ).build_transaction({
            'from': self.address_wallet,
            'value': amount_wei if from_token_symbol.lower() == 'eth' else 0,
            'nonce': self.web3.eth.get_transaction_count(self.web3.to_checksum_address(self.address_wallet)),
            'maxFeePerGas': 0,
            'maxPriorityFeePerGas': 0,
            'gas': 0
        })

        tx.update({'maxFeePerGas': self.web3.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3.eth.gas_price})
        tx.update({'gas': self.web3.eth.estimate_gas(tx)})

        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)
        logger.success(
            f'Swapped {amount} {from_token_symbol} tokens => {to_token_symbol} | '
            f'TX: https://explorer.zksync.io/tx/{tx_hash}')

    async def transfer_to_sender_wallet(self, token_ca):
        nonce = self.web3.eth.get_transaction_count(Web3.to_checksum_address(self.address_wallet))
        usdc_contract = await utils.get_token_contract(self.web3, token_ca)
        usdc_balance = await utils.get_wallet_balance(self.web3, self.address_wallet, token_ca)
        usdc_balance_wei = await utils.amount_to_wei(self.web3, usdc_balance, token_ca)
        tx = usdc_contract.functions.transfer(*(
            Web3.to_checksum_address(self.address_wallet),
            usdc_balance_wei
        )).build_transaction({
            'chainId': self.chain_id,
            'from': self.address_wallet,
            'nonce': nonce,
            'maxFeePerGas': 0,
            'maxPriorityFeePerGas': 0,
            'gas': 0
        })

        tx.update({'maxFeePerGas': self.web3.eth.gas_price})
        tx.update({'maxPriorityFeePerGas': self.web3.eth.gas_price})
        tx.update({'gas': self.web3.eth.estimate_gas(tx)})

        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(raw_tx_hash)
        logger.success(
            f'Transferred to itself {usdc_balance} USDC tokens | '
            f'{self.address_wallet} | '
            f'TX: https://explorer.zksync.io/tx/{tx_hash}'
        )
