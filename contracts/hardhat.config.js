require("@nomicfoundation/hardhat-toolbox");
// Bare dotenv.config() resolves .env against process.cwd(), which breaks
// when this is invoked from within contracts/ (as `npx hardhat run
// scripts/deploy.js` normally is) — the real .env lives one level up, at
// the repo root, alongside routes/mcp/chain.
require("dotenv").config({ path: require("path").join(__dirname, "..", ".env") });

const GIWA_ORACLE_PRIVATE_KEY = process.env.GIWA_ORACLE_PRIVATE_KEY || "";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    // 0.8.24, not 0.8.20 — @openzeppelin/contracts v5's ERC721.sol requires
    // a ^0.8.24 compiler. The contract's own pragma (^0.8.20) still allows
    // this newer compiler; only the pinned compiler version needed bumping.
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      // OpenZeppelin v5 uses MCOPY (EIP-5656, Cancun). GIWA is an OP-Stack
      // chain on a Cancun-or-later execution client, so this is safe.
      evmVersion: "cancun",
    },
  },
  networks: {
    // GIWA Sepolia — OP-Stack EVM L2 testnet. Standard EIP-1559 gas, no
    // Hedera-style hardcoded gasPrice/gasLimit workaround needed (unlike
    // the old hederaTestnet config this replaces, which required both
    // because the hashio relay couldn't reliably estimate gas).
    giwaSepolia: {
      url: "https://sepolia-rpc.giwa.io/",
      chainId: 91342,
      accounts: GIWA_ORACLE_PRIVATE_KEY ? [GIWA_ORACLE_PRIVATE_KEY] : [],
    },
  },
  paths: {
    sources: "./src",
    artifacts: "./artifacts",
    cache: "./cache",
    tests: "./tests",
  },
};
