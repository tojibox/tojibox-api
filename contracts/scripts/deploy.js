/**
 * Deploy TogiboxReportReceipt to GIWA Sepolia.
 *
 * Usage:
 *   npx hardhat run scripts/deploy.js --network giwaSepolia
 *
 * Required env vars:
 *   GIWA_ORACLE_PRIVATE_KEY — deployer account private key (hex, no 0x prefix)
 *   GIWA_ORACLE_ADDRESS     — EOA the oracle API uses to call mintReceipt()
 *                             (can be same as deployer for testnet)
 *
 * After deploy, copy CONTRACT_ADDRESS into togibox-api/.env as
 * TOGIBOX_REPORT_RECEIPT_ADDRESS
 */

const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();

  console.log("Deploying TogiboxReportReceipt...");
  console.log("  Network  :", hre.network.name);
  console.log("  Deployer :", deployer.address);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log("  Balance  :", hre.ethers.formatEther(balance), "ETH");

  // Oracle address — who is allowed to call mintReceipt()
  // For testnet: use same deployer address
  // For production: use the oracle API's dedicated EOA
  const oracleAddress = process.env.GIWA_ORACLE_ADDRESS || deployer.address;
  console.log("  Oracle   :", oracleAddress);

  const TogiboxReportReceipt = await hre.ethers.getContractFactory("TogiboxReportReceipt");

  // GIWA is a standard OP-Stack RPC — let ethers estimate gas, no
  // hardcoded gasPrice/gasLimit override needed (unlike the Hedera
  // hashio-relay deploy script this replaces).
  const contract = await TogiboxReportReceipt.deploy(oracleAddress);
  await contract.waitForDeployment();

  const address = await contract.getAddress();

  console.log("\nTogiboxReportReceipt deployed!");
  console.log("  Contract address :", address);
  console.log("  Oracle address   :", oracleAddress);
  console.log("\nAdd to togibox-api/.env:");
  console.log(`  TOGIBOX_REPORT_RECEIPT_ADDRESS=${address}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
