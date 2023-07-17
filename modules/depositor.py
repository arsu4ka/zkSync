from web3 import Web3
from utils import helper
from loguru import logger


class Depositor:
    def __init__(
            self,
            private_key: str,
    ) -> None:
        self.private_key = private_key

    async def deposit_eth_to_zksync(
            self,
            eth_node,
            eth_amount: float,
            contract_address: str,
            contract_abi_name: str
    ):
        web3 = Web3(Web3.HTTPProvider(eth_node))
        account = web3.eth.account.from_key(self.private_key)
        sender_address = account.address
        contract = await helper.load_contract(
            contract_address,
            web3,
            contract_abi_name
        )

        tx = contract.functions.depositETH(**{
            "_zkSyncAddress": sender_address
        }).build_transaction({
            "from": sender_address,
            "nonce": web3.eth.get_transaction_count(Web3.to_checksum_address(sender_address)),
            "value": web3.to_wei(eth_amount, "ether"),
            'gas': 0
        })
        tx.update({'gas': web3.eth.estimate_gas(tx)})

        signed_tx = web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = web3.to_hex(raw_tx_hash)

        logger.success(
            f"Successfully deposited {eth_amount} ETH | TX: {tx_hash}"
        )

    async def deposit_arbitrum_usdc_to_zksync(
            self,
            usdc_amount: float,
            usdc_fee: float,
            amount_suffix: int,
            arbitrum_node: str,
            arbitrum_contract_address: str,
            arbitrum_abi: str
    ):
        web3 = Web3(Web3.HTTPProvider(arbitrum_node))
        account = web3.eth.account.from_key(self.private_key)
        sender_address = account.address
        contract = await helper.load_contract(
            arbitrum_contract_address,
            web3,
            arbitrum_abi
        )

        tx = contract.functions.transfer(**{
            'recipient': '0x41d3D33156aE7c62c094AAe2995003aE63f587B3',
            'amount': Web3.to_wei(usdc_amount + usdc_fee, "ether") // (10 ** 12) + amount_suffix
        }).build_transaction({
            "from": sender_address,
            "nonce": web3.eth.get_transaction_count(Web3.to_checksum_address(sender_address)),
            "value": 0,
            'gas': 0
        })
        tx.update({'gas': web3.eth.estimate_gas(tx)})

        signed_tx = web3.eth.account.sign_transaction(tx, self.private_key)
        raw_tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = web3.to_hex(raw_tx_hash)

        logger.success(
            f"Successfully deposited {usdc_amount} USDC | TX: {tx_hash}"
        )
        