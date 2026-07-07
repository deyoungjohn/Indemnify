import uuid
from eth_abi.packed import encode_packed
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_bytes, to_checksum_address, keccak
from daemon.config import settings

class CryptographicSigner:
    """
    Cryptographic Signer implementing EIP-191 signing for Project Indemnify.
    Matches the exact abi.encodePacked and toEthSignedMessageHash expectations of ParametricEscrow.sol.
    """

    def __init__(self, private_key: str = None):
        self.private_key = private_key or settings.oracle_private_key
        # Clean the private key format
        if self.private_key.startswith("0x"):
            self.private_key = self.private_key[2:]
        self.account = Account.from_key(self.private_key)
        self.address = self.account.address

    def get_oracle_address(self) -> str:
        """Returns the oracle's public address."""
        return self.address

    def generate_quote_id(self) -> bytes:
        """Generates a cryptographically secure bytes32 quote ID using UUIDv4."""
        return uuid.uuid4().bytes + uuid.uuid4().bytes

    def sign_quote(
        self,
        client_address: str,
        asset: str,
        coverage_amount: int,
        premium_amount: int,
        timeout_duration: int,
        deadline: int,
        quote_id: bytes,
        chain_id: int = None,
        escrow_address: str = None
    ) -> str:
        """
        Signs an insurance quote.
        Matches ParametricEscrow.sol:164 message hash recovery digest.
        """
        chain_id = chain_id if chain_id is not None else settings.chain_id
        escrow_address = escrow_address if escrow_address is not None else settings.escrow_address

        # Standardize addresses
        client_address_clean = to_checksum_address(client_address)
        asset_clean = to_checksum_address(asset)
        escrow_address_clean = to_checksum_address(escrow_address)

        # abi.encodePacked matching
        # msg.sender, asset, coverageAmount, premiumAmount, timeoutDuration, deadline, quoteId, block.chainid, address(this)
        packed_data = encode_packed(
            ['address', 'address', 'uint256', 'uint256', 'uint256', 'uint256', 'bytes32', 'uint256', 'address'],
            [
                client_address_clean,
                asset_clean,
                coverage_amount,
                premium_amount,
                timeout_duration,
                deadline,
                quote_id,
                chain_id,
                escrow_address_clean
            ]
        )

        # Calculate message hash
        message_hash = keccak(packed_data)

        # Convert to EIP-191 signed message format (adds prefix and hashes again)
        signable_message = encode_defunct(primitive=message_hash)
        
        # Sign the message
        signed_message = Account.sign_message(signable_message, self.private_key)
        return signed_message.signature.hex()

    def sign_settlement(
        self,
        policy_id: int,
        tier: int,
        chain_id: int = None,
        escrow_address: str = None
    ) -> str:
        """
        Signs a policy settlement proof.
        Matches ParametricEscrow.sol:276 message hash recovery digest.
        """
        chain_id = chain_id if chain_id is not None else settings.chain_id
        escrow_address = escrow_address if escrow_address is not None else settings.escrow_address

        escrow_address_clean = to_checksum_address(escrow_address)

        # abi.encodePacked matching
        # policyId, tier, block.chainid, address(this)
        packed_data = encode_packed(
            ['uint256', 'uint8', 'uint256', 'address'],
            [
                policy_id,
                tier,
                chain_id,
                escrow_address_clean
            ]
        )

        # Calculate message hash
        message_hash = keccak(packed_data)

        # Convert to EIP-191 signed message format
        signable_message = encode_defunct(primitive=message_hash)

        # Sign the message
        signed_message = Account.sign_message(signable_message, self.private_key)
        return signed_message.signature.hex()
