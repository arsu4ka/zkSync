from web3 import Web3
from utils import helper
from loguru import logger


class Depositor:
    def __init__(
            self,
            private_key: str,
            rpc_chain: str,
    ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_chain))
        self.private_key = private_key
        self.account = self.web3.eth.account.from_key(private_key)
        self.sender_address = self.account.address

    async def deposit_eth_to_zksync(
            self,
            eth_amount: float,
            contract_address: str,
            contract_abi_name: str
    ):
        contract = await helper.load_contract(
            contract_address,
            self.web3,
            contract_abi_name
        )

        tx = contract.functions.depositETH(**{
            "_zkSyncAddress": self.sender_address
        }).build_transaction({
            "from": self.sender_address,
            "nonce": self.web3.eth.get_transaction_count(Web3.to_checksum_address(self.sender_address)),
            "value": self.web3.to_wei(eth_amount, "ether"),
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
            f"Successfully deposited {eth_amount} ETH | TX: {tx_hash}"
        )

    async def deposit_arbitrum_eth_to_zksync(
            self,
            eth_amount: float,
            arbitrum_contract_address: str,
            arbitrum_abi: str
    ):
        contract = await helper.load_contract(
            arbitrum_contract_address,
            self.web3,
            arbitrum_abi
        )

        tx = contract.functions.transfer(**{
            # 'recipient': "",
            # 'amount': int(str(Web3.to_wei(amount + 1.8, 'ether') // 1000000000000)[:-4] + '9003')
        }).build_transaction({
            "from": self.sender_address,
            "nonce": self.web3.eth.get_transaction_count(Web3.to_checksum_address(self.sender_address)),
            "value": 0,
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
            f"Successfully deposited {eth_amount} ETH | TX: {tx_hash}"
        )