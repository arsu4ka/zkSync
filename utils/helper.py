from web3.contract import Contract
from eth_typing import Address
from eth_typing import HexStr
import asyncio
import random
import json
from hexbytes import HexBytes
from loguru import logger
from web3 import Web3
import os


async def get_nft_id(web3: Web3, tx_hash: str) -> int:
    logs = web3.eth.get_transaction_receipt(HexBytes(tx_hash)).logs

    for log in logs:
        if 'topics' in log and len(log['topics']) > 3:
            topic = log['topics'][3]
            if isinstance(topic, HexBytes):
                nft_id = int(topic.hex(), 16)
                return nft_id


async def setup_tokens_addresses(token1_symbol: str, token2_symbol: str, tokens: dict[str, str]) \
        -> tuple[str, str] | None:
    token1_symbol, token2_symbol = token1_symbol.upper(), token2_symbol.upper()
    if not (token1_symbol in tokens and token2_symbol in tokens):
        logger.error(f"No addresses found for such tokens")
        return None
    token1_address, token2_address = tokens[token1_symbol], tokens[token2_symbol]
    return token1_address, token2_address


async def setup_transaction_data(web3: Web3, from_token_address: str, to_token_address: str) -> tuple[str, int, str]:
    if from_token_address == '':
        from_token_address = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'
        from_decimals = 18

    else:
        from_decimals = await check_data_token(web3, from_token_address)

    if to_token_address == '':
        to_token_address = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'

    return from_token_address, from_decimals, to_token_address


async def check_data_token(web3: Web3, from_token_address: str) -> int:
    try:
        token_contract = web3.eth.contract(address=web3.to_checksum_address(from_token_address),
                                           abi=await load_abi('erc20'))
        decimals = token_contract.functions.decimals().call()

        return decimals

    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def create_amount(token: str, web3: Web3, token_address, amount: float) -> tuple[float, Contract | None]:
    if token.lower() != 'eth':
        stable_contract = await load_contract(token_address, web3, 'erc20')

        if token.lower() == 'usdc' or token.lower() == 'usdt':
            amount = amount * 10 ** 6
            return amount, stable_contract
        else:
            amount = web3.to_wei(amount, 'ether') // 10000 * 10000
            return amount, stable_contract

    else:
        stable_contract = await load_contract(token_address, web3, 'erc20')
        amount = amount * 10 ** 18

    return amount, stable_contract


async def load_contract(address, web3, abi_name) -> Contract | None:
    if address is None:
        return

    address = web3.to_checksum_address(address)
    return web3.eth.contract(address=address, abi=await load_abi(abi_name))


async def load_abi(name: str) -> str:
    file_name = os.path.join(os.getcwd(), f'utils/abis/{name}.json')
    with open(file_name) as f:
        abi: str = json.load(f)
    return abi


async def get_wallet_balance(token: str, w3: Web3, address: Address, stable_contract: Contract | None,
                             from_chain: str) -> float:
    if token.lower() != 'eth':
        balance = stable_contract.functions.balanceOf(address).call()
        return balance
    else:
        if from_chain == 'bsc' or from_chain == 'matic':
            balance = stable_contract.functions.balanceOf(address).call()
        else:
            balance = w3.eth.get_balance(address)

        return balance


async def approve_token(
        amount: float,
        private_key: str,
        chain: str,
        from_token_address: str,
        from_token_symbol: str,
        spender: str,
        address_wallet: Address,
        web3: Web3
        ) -> HexStr:
    try:
        spender = web3.to_checksum_address(spender)
        contract = await get_contract(web3, from_token_address)
        allowance_amount = await check_allowance(web3, from_token_address, address_wallet, spender)
        diff = amount - allowance_amount

        if diff > 0:
            tx = contract.functions.approve(
                spender,
                amount
            ).build_transaction(
                {
                    'chainId': web3.eth.chain_id,
                    'from': address_wallet,
                    'nonce': web3.eth.get_transaction_count(address_wallet),
                    'gasPrice': 0,
                    'gas': 0,
                    'value': 0
                }
            )
            if chain == 'bsc':
                tx['gasPrice'] = random.randint(1000000000, 1050000000)
            else:
                gas_price = await add_gas_price(web3)
                tx['gasPrice'] = gas_price

            gas_limit = await add_gas_limit(web3, tx)
            tx['gas'] = gas_limit

            signed_tx = web3.eth.account.sign_transaction(tx, private_key=private_key)
            raw_tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_receipt = web3.eth.wait_for_transaction_receipt(raw_tx_hash)
            while tx_receipt is None:
                await asyncio.sleep(1)
                tx_receipt = web3.eth.get_transaction_receipt(raw_tx_hash)
            tx_hash = web3.to_hex(raw_tx_hash)
            logger.info(f'{amount} {from_token_symbol} approved for {address_wallet} wallet | Tx '
                        f'hash: {tx_hash}')
            await asyncio.sleep(5)
            return tx_hash

    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def check_allowance(web3: Web3, from_token_address: str, address_wallet: Address, spender: str) -> float:
    try:
        contract = web3.eth.contract(address=web3.to_checksum_address(from_token_address),
                                     abi=await load_abi('erc20'))
        amount_approved = contract.functions.allowance(address_wallet, spender).call()
        return amount_approved

    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def add_gas_price(web3: Web3) -> int:
    try:
        gas_price = web3.eth.gas_price
        gas_price = int(gas_price * random.uniform(1.01, 1.02))
        return gas_price
    except Exception as ex:
        logger.error(f'Something went wrong | {ex}')


async def add_gas_limit(web3: Web3, tx: dict) -> int:
    tx['value'] = 0
    gas_limit = web3.eth.estimate_gas(tx)

    return gas_limit


async def get_contract(web3: Web3, from_token_address: str) -> Contract:
    return web3.eth.contract(address=web3.to_checksum_address(from_token_address),
                             abi=await load_abi('erc20'))
