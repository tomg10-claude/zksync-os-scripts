#!/usr/bin/env python3
"""
Local state update script for zksync-os-server.

Steps:
- Check env + tooling
- Build zkstack CLI
- Build era-contracts L1, generate genesis.json
- Initialize ecosystem (zksync-os mode)
- Start Anvil, fund accounts, deploy L1 contracts
- Extract bridgehub + operator keys
- Generate L1 -> L2 deposit tx
- Stop Anvil and dump the new zkos-l1-state.json
"""

from pathlib import Path
from packaging.version import Version

from lib.script_context import ScriptCtx
from lib.entry import run_script
from lib import utils
from lib import edit_server
from lib import config
from lib.protocol_version import (
    PROTOCOL_TOOLCHAINS,
    PROTOCOL_VERSION_NEXT,
)


# ---------------------------------------------------------------------------
# Funded accounts
# ---------------------------------------------------------------------------
RICH_WALLETS: list[tuple[str, str]] = [
    ("0xa61464658afeaf65cccaafd3a512b69a83b77618", "9000 ETH"),
    ("0x36615cf349d7f6344891b1e7ca7c72883f5dc049", "9000 ETH"),
]


# ---------------------------------------------------------------------------
# Funding logic
# ---------------------------------------------------------------------------
def fund_accounts(ctx: ScriptCtx, ecosystem_dir: Path) -> None:
    """
    Approximate port of the bash funding logic:
    - Find all wallets.yaml
    - For each, extract addresses and send 100 ETH
    - Then fund two (hardcoded) rich wallets with 9000 ETH each
    """

    if not ecosystem_dir.is_dir():
        ctx.fail(f"Ecosystem dir not found: {ecosystem_dir}")

    wallets_files = list(ecosystem_dir.rglob("wallets.yaml"))
    if not wallets_files:
        ctx.fail(f"No wallets.yaml found under {ecosystem_dir}")

    all_addrs: set[str] = set()
    for wf in wallets_files:
        data = utils.load_yaml(wf)
        addrs = utils.addresses_from_wallets_yaml(data)
        if addrs:
            ctx.logger.debug(f"Found {len(addrs)} addresses in {wf}")
            all_addrs.update(addrs)

    rpc_url: str = config.ANVIL_DEFAULT_URL

    # Fund each address
    ctx.logger.debug(f"Funding {len(all_addrs)} addresses with 100 ETH each...")
    amount_100eth = hex(100 * 10**18)
    for addr in sorted(all_addrs):
        ctx.sh(
            f"cast rpc anvil_setBalance {addr} {amount_100eth} --rpc-url {rpc_url}",
            print_command=False,
        )

    # Two large transfers between rich wallets
    ctx.logger.debug("Funding two rich wallets with 9000 ETH each...")
    amount_9000eth = hex(9000 * 10**18)
    for addr, _ in RICH_WALLETS:
        ctx.sh(
            f"cast rpc anvil_setBalance {addr} {amount_9000eth} --rpc-url {rpc_url}",
            print_command=False,
        )



def _funded_accounts_section(wallets_files: list[Path]) -> str:
    """
    Build the "Funded Accounts" Markdown section from wallets YAML files
    and the hardcoded rich wallets.
    """
    lines: list[str] = [
        "## Funded Accounts",
        "",
        "The following accounts are pre-funded on the local L1 (Anvil).",
        "",
    ]

    # Collect addresses from wallets.yaml files
    all_addrs: set[str] = set()
    for wf in wallets_files:
        data = utils.load_yaml(wf)
        all_addrs.update(utils.addresses_from_wallets_yaml(data))

    if all_addrs:
        lines.append("### Wallet accounts (100 ETH each)")
        lines.append("")
        lines.append("| Address | Balance |")
        lines.append("|---------|---------|")
        for addr in sorted(all_addrs):
            lines.append(f"| `{addr}` | 100 ETH |")
        lines.append("")

    lines.append("### Rich wallets")
    lines.append("")
    lines.append("| Address | Balance |")
    lines.append("|---------|---------|")
    for addr, balance in RICH_WALLETS:
        lines.append(f"| `{addr}` | {balance} |")
    lines.append("")

    return "\n".join(lines)


def generate_readme(
    readme_path: Path,
    *,
    protocol_version: str,
    ecosystem_name: str,
    chains: list[str],
    wallets_files: list[Path],
) -> None:
    """Generate a README.md for a local-chains setup directory."""
    funded_section = _funded_accounts_section(wallets_files)

    if ecosystem_name == "multi_chain":
        title = f"Multiple Chains ({protocol_version})"
        desc = "Configuration for running multiple ZKsync OS chains against a shared L1."
        chain_rows = "\n".join(
            f"| `chain_{c}.yaml` | {c}     | {3050 + i}     |"
            for i, c in enumerate(chains)
        )
        quick_start = (
            f"```bash\n"
            f"# Use script to launch in-memory L1 and {len(chains)} nodes for all chains\n"
            f"./run_local.sh ./local-chains/{protocol_version}/{ecosystem_name}\n"
            f"```"
        )
        wallets_links = "\n".join(
            f"* [wallets_{c}.yaml](./wallets_{c}.yaml)" for c in chains
        )
        wallets_section = (
            f"## Wallets\n\n"
            f"For complete list of keys and wallet addresses, check:\n"
            f"{wallets_links}\n"
            f"for the corresponding chain."
        )
        contracts_links = "\n".join(
            f"* [chain_{c}.yaml](./chain_{c}.yaml)" for c in chains
        )
        contracts_section = (
            f"## Contract Addresses\n\n"
            f"For contract addresses, please refer to `genesis` section of:\n"
            f"{contracts_links}\n"
            f"for the corresponding chain."
        )
    else:
        title = f"Single Chain ({protocol_version})"
        desc = f"Default single-chain configuration for running ZKsync OS against L1 for protocol version {protocol_version}."
        chain_rows = f"| `config.yaml`     | {chains[0]}     | 3050     |"
        quick_start = (
            f"```bash\n"
            f"# Use script to launch in-memory L1 and the node for one chain\n"
            f"./run_local.sh ./local-chains/{protocol_version}/{ecosystem_name}\n"
            f"```"
        )
        wallets_section = (
            "## Wallets\n\n"
            "For complete list of keys and wallet addresses, check [wallets.yaml](./wallets.yaml)."
        )
        contracts_section = (
            "## Contract Addresses\n\n"
            "For contract addresses, refer to `genesis` section of the [config.yaml](./config.yaml)."
        )

    content = f"""\
# {title}

{desc}

## Chains

| Config            | Chain ID | RPC Port |
|-------------------|----------|----------|
{chain_rows}

## Quick Start

{quick_start}

{wallets_section}

{contracts_section}

{funded_section}
## Versions

For information about how this config was created, check [versions.yaml](../versions.yaml) file.
"""
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(content, encoding="utf-8")


def init_ecosystem(
    ctx: ScriptCtx,
    ecosystem_name: str,
    chains: list[str],
) -> None:
    era_contracts_path = utils.require_path("ERA_CONTRACTS_PATH")
    zksync_era_path = utils.require_path("ZKSYNC_ERA_PATH")
    protocol_version = utils.require_env("PROTOCOL_VERSION")

    zkstack_bin = zksync_era_path / "zkstack_cli" / "target" / "release" / "zkstack"
    ecosystems_dir = ctx.workspace / "ecosystems"
    ecosystem_dir = ctx.workspace / "ecosystems" / ecosystem_name
    protocol_base = ctx.repo_dir / "local-chains" / protocol_version
    default_base = protocol_base / "default"
    base = protocol_base / ecosystem_name

    with ctx.section(f"Initialize {ecosystem_name} ecosystem", expected=120):
        utils.clean_dir(ecosystem_dir)
        ctx.sh(
            f"""
                {zkstack_bin}
                  ecosystem create
                  --ecosystem-name {ecosystem_name}
                  --l1-network localhost
                  --chain-name tmp-chain
                  --chain-id 12345
                  --prover-mode no-proofs
                  --wallet-creation random
                  --link-to-code {zksync_era_path}
                  --l1-batch-commit-data-generator-mode rollup
                  --start-containers false
                  --base-token-address 0x0000000000000000000000000000000000000001
                  --base-token-price-nominator 1
                  --base-token-price-denominator 1
                  --evm-emulator false
                """,
            cwd=ecosystems_dir,
        )
        ctx.sh(
            f"""
                {zkstack_bin}
                  ctm set-ctm-contracts
                  --contracts-src-path {era_contracts_path}
                  --default-configs-src-path {ctx.repo_dir / "local-chains" / protocol_version / "default"}
                  --zksync-os
                """,
            cwd=ecosystem_dir,
        )
        # Remove default era chain (non zksync-os)
        utils.clean_dir(ecosystem_dir / "chains")

        for chain in chains:
            ctx.sh(
                f"""
                {zkstack_bin}
                  chain create
                  --chain-name {chain}
                  --chain-id {chain}
                  --prover-mode no-proofs
                  --wallet-creation random
                  --l1-batch-commit-data-generator-mode rollup
                  --base-token-address 0x0000000000000000000000000000000000000001
                  --base-token-price-nominator 1
                  --base-token-price-denominator 1
                  --evm-emulator false
                  --set-as-default=true
                  --zksync-os
                """,
                cwd=ecosystem_dir,
            )

    # ------------------------------------------------------------------ #
    # Start Anvil
    # ------------------------------------------------------------------ #
    with ctx.section(f"Generating l1-state.json for {ecosystem_name}", expected=250):
        l1_state_file = protocol_base / "l1-state.json"
        with utils.anvil_dump_state(l1_state_file=l1_state_file):
            # ------------------------------------------------------------------ #
            # Fund accounts
            # ------------------------------------------------------------------ #
            ctx.logger.info("Funding accounts...")
            fund_accounts(ctx, ecosystem_dir)
            # ------------------------------------------------------------------ #
            # Deploy L1 contracts via zkstack
            # ------------------------------------------------------------------ #
            ctx.logger.info("Deploying L1 contracts...")
            ctx.sh(
                f"""
                    {zkstack_bin}
                      ecosystem init
                      --deploy-paymaster=false
                      --deploy-erc20=false
                      --observability=false
                      --no-port-reallocation
                      --deploy-ecosystem
                      --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                      --zksync-os
                    """,
                cwd=ecosystem_dir,
            )
            for chain in chains:
                # ------------------------------------------------------------------ #
                # Update contract addresses and operator keys
                # ------------------------------------------------------------------ #
                ctx.logger.debug("Updating contract addresses...")
                contracts_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "contracts.yaml"
                )
                chain_wallets_yaml = (
                    ecosystem_dir / "chains" / chain / "configs" / "wallets.yaml"
                )
                edit_server.update_chain_config_yaml(
                    base / f"chain_{chain}.yaml",
                    contracts_yaml=contracts_yaml,
                    wallets_yaml=chain_wallets_yaml,
                )
                name_suffix = f"_{chain}" if ecosystem_name == "multi_chain" else ""
                wallets_out = base / f"wallets{name_suffix}.yaml"
                contracts_out = base / f"contracts{name_suffix}.yaml"
                # Copy wallets.yaml and contracts.yaml to local-chains
                utils.cp(chain_wallets_yaml, wallets_out)
                utils.cp(contracts_yaml, contracts_out)
                # ------------------------------------------------------------------ #
                # Generate deposit transaction
                # ------------------------------------------------------------------ #
                ctx.logger.info("Generating L1 -> L2 deposit transaction...")
                bridgehub_address = edit_server.get_contract_address(
                    contracts_yaml,
                    "bridgehub_proxy_addr",
                )
                ctx.sh(
                    f"""
                    cargo run --release --package zksync_os_generate_deposit --
                    --bridgehub "{bridgehub_address}"
                    --chain-id {chain}
                    --amount 100
                    """
                )
                if chain == config.GATEWAY_CHAIN_ID:
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway create-tx-filterer
                            --chain {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --ignore-prerequisites
                            """,
                        cwd=ecosystem_dir,
                    )
                    ctx.sh(
                        f"""
                            {zkstack_bin}
                            chain gateway convert-to-gateway
                            --chain {config.GATEWAY_CHAIN_ID}
                            --l1-rpc-url="{config.ANVIL_DEFAULT_URL}"
                            --ignore-prerequisites
                            --no-gateway-overrides
                            """,
                        cwd=ecosystem_dir,
                    )
            # ------------------------------------------------------------------ #
            # Generate README with funded account information
            # ------------------------------------------------------------------ #
            all_wallets_files = list(ecosystem_dir.rglob("wallets.yaml"))
            generate_readme(
                base / "README.md",
                protocol_version=protocol_version,
                ecosystem_name=ecosystem_name,
                chains=chains,
                wallets_files=all_wallets_files,
            )

            # Also generate for default setup (symlinks to first chain)
            default_wallets = [
                ecosystem_dir / "chains" / chains[0] / "configs" / "wallets.yaml"
            ]
            generate_readme(
                default_base / "README.md",
                protocol_version=protocol_version,
                ecosystem_name="default",
                chains=[chains[0]],
                wallets_files=default_wallets,
            )

            # Update Default setup with information from the first chain in the list
            # TODO: temporarily we are reusing one of the chains from Multichain setup for the Default setup
            contracts_yaml = (
                ecosystem_dir / "chains" / chains[0] / "configs" / "contracts.yaml"
            )
            chain_wallets_yaml = (
                ecosystem_dir / "chains" / chains[0] / "configs" / "wallets.yaml"
            )
            edit_server.update_chain_config_yaml(
                default_base / "config.yaml",
                contracts_yaml=contracts_yaml,
                wallets_yaml=chain_wallets_yaml,
            )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def script(ctx: ScriptCtx) -> None:
    # Paths & constants
    era_contracts_path: Path = utils.require_path("ERA_CONTRACTS_PATH")
    zksync_era_path: Path = utils.require_path("ZKSYNC_ERA_PATH")
    protocol_version: str = utils.require_env("PROTOCOL_VERSION")
    try:
        toolchain = PROTOCOL_TOOLCHAINS[protocol_version]
    except KeyError:
        raise ValueError(
            f"Unsupported PROTOCOL_VERSION: {protocol_version}. Supported: {list(PROTOCOL_TOOLCHAINS.keys())}"
        )
    execution_version: str = toolchain.execution_version
    proving_version: str = toolchain.proving_version
    cast_forge_version: str = toolchain.cast_forge_version
    anvil_version: str = toolchain.anvil_version
    cargo_version: str = toolchain.cargo_version
    yarn_version: str = toolchain.yarn_version

    # ------------------------------------------------------------------ #
    # Tooling check
    # ------------------------------------------------------------------ #
    utils.require_cmds(
        {
            "yarn": f">={yarn_version}",
            "anvil": f"=={anvil_version}",
            "cast": f"=={cast_forge_version}",
            "forge": f"=={cast_forge_version}",
            "cargo": f">={cargo_version}",
        }
    )

    # TODO: remove this later, needs only for v31 for now
    # ------------------------------------------------------------------ #
    # Build contracts for zkstack (temporary)
    # ------------------------------------------------------------------ #
    if Version(protocol_version) >= Version(PROTOCOL_VERSION_NEXT):
        zkstack_era_contracts_path: Path = zksync_era_path / "contracts"
        with ctx.section("Build contracts in zkstack", expected=120):
            ctx.sh(
                """
                yarn install
                """,
                cwd=zkstack_era_contracts_path,
            )
            ctx.sh(
                """
                yarn build:foundry
                """,
                cwd=zkstack_era_contracts_path / "da-contracts",
            )
            ctx.sh(
                """
                yarn build:foundry
                """,
                cwd=zkstack_era_contracts_path / "l1-contracts",
            )

    # ------------------------------------------------------------------ #
    # Build contracts
    # ------------------------------------------------------------------ #
    with ctx.section("Build contracts", expected=120):
        ctx.sh(
            """
            yarn install
            """,
            cwd=era_contracts_path,
        )
        ctx.sh(
            """
            yarn build:foundry
            """,
            cwd=era_contracts_path / "da-contracts",
        )
        ctx.sh(
            """
            yarn build:foundry
            """,
            cwd=era_contracts_path / "l1-contracts",
        )

    # ------------------------------------------------------------------ #
    # Build zkstack CLI
    # ------------------------------------------------------------------ #
    with ctx.section("Build zkstack CLI", expected=100):
        ctx.sh(
            """
            cargo build --release --bin zkstack
            """,
            cwd=zksync_era_path / "zkstack_cli",
        )

    # ------------------------------------------------------------------ #
    # Generate genesis.json
    # ------------------------------------------------------------------ #
    with ctx.section("Generate genesis.json", expected=60):
        ctx.sh(
            f"""
            cargo run --
              --output-file {ctx.repo_dir / "local-chains" / protocol_version / "genesis.json"}
              --execution-version {execution_version}
            """,
            cwd=era_contracts_path / "tools" / "zksync-os-genesis-gen",
        )

    # TODO: currently single-chain setup is disabled, instead it is a symlink to one of the chains from Multi-chain setup
    #       this might change in the future
    # # ------------------------------------------------------------------ #
    # # Single-chain setup
    # # ------------------------------------------------------------------ #
    # init_ecosystem(ctx, "default", ["6565"])

    # ------------------------------------------------------------------ #
    # Multi-chain setup
    # ------------------------------------------------------------------ #
    # TODO: uncomment when gateway chain is supported in main server
    init_ecosystem(ctx, "multi_chain", ["6565", "6566"])
    # if Version(protocol_version) == Version(PROTOCOL_VERSION_CURRENT):
    #     init_ecosystem(ctx, "multi_chain", ["6565", "6566"])

    # if Version(protocol_version) >= Version(PROTOCOL_VERSION_NEXT):
    #     init_ecosystem(ctx, "multi_chain", ["6565", "6566", config.GATEWAY_CHAIN_ID])

    # ------------------------------------------------------------------ #
    # Update VK hash in prover config
    # ------------------------------------------------------------------ #
    edit_server.update_vk_hash(
        ctx.repo_dir / "lib" / "types" / "src" / "protocol" / "proving_version.rs",
        era_contracts_path
        / "l1-contracts"
        / "contracts"
        / "state-transition"
        / "verifiers"
        / "ZKsyncOSVerifierPlonk.sol",
        proving_version,
    )

    # ------------------------------------------------------------------ #
    # Regenerate contracts.json
    # ------------------------------------------------------------------ #
    with ctx.section("Regenerate contracts.json", expected=30):
        ctx.sh("yarn install", cwd=era_contracts_path / "l1-contracts")
        ctx.sh(
            f"""
            yarn write-factory-deps-zksync-os
            --output {ctx.repo_dir}/lib/l1_watcher/src/factory_deps/contracts.json
            """,
            cwd=era_contracts_path / "l1-contracts",
        )


if __name__ == "__main__":
    run_script(
        script,
        required_env=(
            "ERA_CONTRACTS_PATH",
            "ZKSYNC_ERA_PATH",
            "PROTOCOL_VERSION",
        ),
    )
