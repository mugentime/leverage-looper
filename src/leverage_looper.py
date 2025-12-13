"""
Leverage Looper - Maximizes leverage on existing loan positions through recursive loops

Strategy:
- For each position: borrow at 75% LTV -> convert to collateral -> add collateral -> repeat
- Target: MAXIMUM leverage (no cap)
- Stop when: borrow amount < $1 OR spot reserve reaches $10
- After looping: sweep remaining spot balances into collateral
"""
import asyncio
from typing import Dict, List, Optional
from loguru import logger

TARGET_LTV = 0.75
MIN_LOOP_USD = 1.0      # Minimum $1 per loop to avoid dust
MIN_RESERVE_USD = 10.0  # Keep $10 minimum in spot
API_DELAY = 1.5         # Seconds between API calls


class LeverageLooper:
    """Maximizes leverage on existing loan positions through recursive loops"""

    def __init__(self, client):
        self.client = client

    async def _get_price(self, coin: str) -> float:
        """Get price for a coin (1.0 for USDT)"""
        if coin == 'USDT':
            return 1.0
        try:
            return await self.client.get_price(f'{coin}USDT')
        except Exception as e:
            logger.warning(f"Could not get price for {coin}: {e}")
            return 0.0

    async def get_total_spot_value(self) -> float:
        """Get total USD value of all spot balances"""
        balances = await self.client.get_all_spot_balances()
        total = 0.0
        for asset, amount in balances.items():
            if asset.startswith('LD'):  # Skip Simple Earn tokens
                continue
            if asset == 'USDT':
                total += amount
            else:
                price = await self._get_price(asset)
                total += amount * price
        return total

    async def get_current_leverage(self, loan: Dict) -> float:
        """
        Calculate current leverage ratio for a position

        Leverage = collateral_value / (collateral_value - debt_value)
        At 75% LTV: collateral=$100, debt=$75 -> leverage = 100/(100-75) = 4x
        """
        coll_coin = loan.get('collateralCoin')
        loan_coin = loan.get('loanCoin')
        coll_amount = float(loan.get('collateralAmount', 0))
        total_debt = float(loan.get('totalDebt', 0))

        coll_price = await self._get_price(coll_coin)
        loan_price = await self._get_price(loan_coin)

        if coll_price == 0 or loan_price == 0:
            return 0.0

        coll_usd = coll_amount * coll_price
        debt_usd = total_debt * loan_price

        if coll_usd <= debt_usd:
            return 0.0  # Position underwater

        equity = coll_usd - debt_usd
        leverage = coll_usd / equity if equity > 0 else 0.0

        return leverage

    async def calculate_borrow_amount(self, loan: Dict) -> tuple[float, float]:
        """
        Calculate how much more can be borrowed to reach 75% LTV
        Returns: (borrow_amount_in_loan_coin, borrow_usd_value)
        """
        coll_coin = loan.get('collateralCoin')
        loan_coin = loan.get('loanCoin')
        coll_amount = float(loan.get('collateralAmount', 0))
        total_debt = float(loan.get('totalDebt', 0))

        coll_price = await self._get_price(coll_coin)
        loan_price = await self._get_price(loan_coin)

        if coll_price == 0 or loan_price == 0:
            return 0.0, 0.0

        coll_usd = coll_amount * coll_price
        current_debt_usd = total_debt * loan_price
        target_debt_usd = coll_usd * TARGET_LTV
        borrow_usd = target_debt_usd - current_debt_usd

        if borrow_usd < MIN_LOOP_USD:
            return 0.0, 0.0

        borrow_amount = borrow_usd / loan_price
        return borrow_amount, borrow_usd

    async def execute_loop(self, loan: Dict) -> Dict:
        """
        Execute one borrow-convert-deposit loop

        Steps:
        1. Borrow more to reach 75% LTV
        2. Convert borrowed funds to collateral asset
        3. Add converted funds as additional collateral

        Returns: {success: bool, borrowed_usd: float, added_usd: float, error: str}
        """
        coll_coin = loan.get('collateralCoin')
        loan_coin = loan.get('loanCoin')

        result = {
            'success': False,
            'borrowed_usd': 0.0,
            'added_usd': 0.0,
            'error': None
        }

        # Step 1: Calculate and execute borrow
        borrow_amount, borrow_usd = await self.calculate_borrow_amount(loan)

        if borrow_usd < MIN_LOOP_USD:
            result['error'] = f'Borrow amount too small: ${borrow_usd:.2f}'
            return result

        logger.info(f"  Loop: Borrowing {borrow_amount:.4f} {loan_coin} (${borrow_usd:.2f})")

        try:
            await self.client.borrow_flexible_loan_by_amount(
                loan_coin=loan_coin,
                collateral_coin=coll_coin,
                loan_amount=borrow_amount
            )
            result['borrowed_usd'] = borrow_usd
        except Exception as e:
            result['error'] = f'Borrow failed: {e}'
            logger.error(f"  Borrow error: {e}")
            return result

        await asyncio.sleep(API_DELAY)

        # Step 2: Convert borrowed funds to collateral asset
        if coll_coin == loan_coin:
            add_amount = borrow_amount
        else:
            try:
                loan_balance = await self.client.get_spot_balance(loan_coin)
                convert_amount = min(borrow_amount, loan_balance * 0.99)

                if convert_amount < 0.01:
                    result['error'] = f'Insufficient {loan_coin} balance to convert'
                    return result

                convert_usd = convert_amount * await self._get_price(loan_coin)

                if convert_usd >= 5.0:
                    logger.info(f"  Loop: Selling {convert_amount:.4f} {loan_coin} for {coll_coin}")
                    try:
                        if loan_coin != 'USDT' and coll_coin != 'USDT':
                            sell_result = await self.client.market_sell(f'{loan_coin}USDT', convert_amount)
                            usdt_received = float(sell_result.get('cummulativeQuoteQty', 0))
                            await asyncio.sleep(API_DELAY)
                            buy_result = await self.client.market_buy(f'{coll_coin}USDT', usdt_received)
                            add_amount = float(buy_result.get('executedQty', 0))
                        elif loan_coin == 'USDT':
                            buy_result = await self.client.market_buy(f'{coll_coin}USDT', convert_amount)
                            add_amount = float(buy_result.get('executedQty', 0))
                        else:
                            sell_result = await self.client.market_sell(f'{loan_coin}USDT', convert_amount)
                            add_amount = float(sell_result.get('cummulativeQuoteQty', 0))
                    except Exception as e:
                        logger.warning(f"  Market trade failed, trying Convert API: {e}")
                        convert_result = await self.client.convert_asset(loan_coin, coll_coin, convert_amount)
                        if convert_result.get('status') == 'SUCCESS':
                            add_amount = float(convert_result.get('toAmount', 0))
                        else:
                            result['error'] = f'Convert failed: {convert_result}'
                            return result
                else:
                    logger.info(f"  Loop: Converting {convert_amount:.4f} {loan_coin} to {coll_coin}")
                    convert_result = await self.client.convert_asset(loan_coin, coll_coin, convert_amount)
                    if convert_result.get('status') == 'SUCCESS':
                        add_amount = float(convert_result.get('toAmount', 0))
                    else:
                        result['error'] = f'Convert failed: {convert_result}'
                        return result

            except Exception as e:
                result['error'] = f'Convert error: {e}'
                logger.error(f"  Convert error: {e}")
                return result

        await asyncio.sleep(API_DELAY)

        # Step 3: Add converted funds as collateral
        if add_amount < 0.001:
            result['error'] = 'Converted amount too small to add as collateral'
            return result

        coll_price = await self._get_price(coll_coin)
        add_usd = add_amount * coll_price

        logger.info(f"  Loop: Adding {add_amount:.4f} {coll_coin} (${add_usd:.2f}) as collateral")

        try:
            await self.client.adjust_loan_ltv(
                loan_coin=loan_coin,
                collateral_coin=coll_coin,
                adjustment_amount=add_amount,
                direction='ADDITIONAL'
            )
            result['added_usd'] = add_usd
            result['success'] = True
            logger.info(f"  Loop complete: borrowed ${borrow_usd:.2f}, added ${add_usd:.2f}")
        except Exception as e:
            result['error'] = f'Add collateral failed: {e}'
            logger.error(f"  Add collateral error: {e}")

        return result

    async def sweep_spot_to_collateral(self, loans: List[Dict]) -> Dict:
        """
        Sweep ALL remaining spot balances into collateral positions.
        Keep only $10 USDT reserve. Distribute USDT across ALL USDT-collateral positions.

        Returns: {swept_usd: float, positions_swept: int, details: List}
        """
        result = {
            'swept_usd': 0.0,
            'positions_swept': 0,
            'details': []
        }

        # Build map of collateral coins to their positions (can have multiple per coin)
        coll_to_positions = {}
        for loan in loans:
            coll_coin = loan.get('collateralCoin')
            if coll_coin not in coll_to_positions:
                coll_to_positions[coll_coin] = []
            coll_to_positions[coll_coin].append(loan)

        # Get all spot balances
        balances = await self.client.get_all_spot_balances()

        # Process non-USDT assets first
        for asset, amount in balances.items():
            if asset.startswith('LD') or asset == 'USDT':
                continue

            if asset not in coll_to_positions:
                continue

            price = await self._get_price(asset)
            usd_value = amount * price

            if usd_value < 0.10:  # Skip tiny amounts
                continue

            add_amount = amount * 0.99
            loan = coll_to_positions[asset][0]  # Use first position for this collateral
            loan_coin = loan.get('loanCoin')

            logger.info(f"Sweep: Adding {add_amount:.6f} {asset} (${usd_value:.2f}) to {asset}->{loan_coin}")

            try:
                await self.client.adjust_loan_ltv(
                    loan_coin=loan_coin,
                    collateral_coin=asset,
                    adjustment_amount=add_amount,
                    direction='ADDITIONAL'
                )
                result['swept_usd'] += usd_value
                result['positions_swept'] += 1
                result['details'].append({
                    'asset': asset,
                    'amount': add_amount,
                    'usd': usd_value,
                    'position': f"{asset}->{loan_coin}"
                })
                logger.info(f"  Swept {add_amount:.6f} {asset} successfully")
            except Exception as e:
                logger.warning(f"  Sweep failed for {asset}: {e}")

            await asyncio.sleep(API_DELAY)

        # Now process USDT - distribute across ALL USDT-collateral positions
        usdt_balance = balances.get('USDT', 0)
        usdt_available = max(0, usdt_balance - MIN_RESERVE_USD)

        if usdt_available >= 1 and 'USDT' in coll_to_positions:
            usdt_positions = coll_to_positions['USDT']
            num_positions = len(usdt_positions)
            usdt_per_position = usdt_available / num_positions

            logger.info(f"Sweep: Distributing ${usdt_available:.2f} USDT across {num_positions} positions (${usdt_per_position:.2f} each)")

            for loan in usdt_positions:
                loan_coin = loan.get('loanCoin')
                add_amount = usdt_per_position

                if add_amount < 0.50:
                    continue

                logger.info(f"  Adding {add_amount:.2f} USDT to USDT->{loan_coin}")

                try:
                    await self.client.adjust_loan_ltv(
                        loan_coin=loan_coin,
                        collateral_coin='USDT',
                        adjustment_amount=add_amount,
                        direction='ADDITIONAL'
                    )
                    result['swept_usd'] += add_amount
                    result['positions_swept'] += 1
                    result['details'].append({
                        'asset': 'USDT',
                        'amount': add_amount,
                        'usd': add_amount,
                        'position': f"USDT->{loan_coin}"
                    })
                    logger.info(f"  Swept {add_amount:.2f} USDT to USDT->{loan_coin} successfully")
                except Exception as e:
                    logger.warning(f"  Sweep USDT to {loan_coin} failed: {e}")

                await asyncio.sleep(API_DELAY)

        return result

    async def loop_position(self, loan: Dict) -> Dict:
        """
        Run loops on a single position until borrow amount < $1
        NO LEVERAGE CAP - maximize everything
        """
        coll_coin = loan.get('collateralCoin')
        loan_coin = loan.get('loanCoin')
        position_name = f"{coll_coin}->{loan_coin}"

        initial_leverage = await self.get_current_leverage(loan)

        result = {
            'position': position_name,
            'initial_leverage': initial_leverage,
            'final_leverage': initial_leverage,
            'loops_executed': 0,
            'total_borrowed_usd': 0.0,
            'total_added_usd': 0.0,
            'errors': []
        }

        logger.info(f"{position_name}: Starting at {initial_leverage:.2f}x leverage")

        max_loops = 50  # Safety limit
        loop_count = 0

        while loop_count < max_loops:
            loop_count += 1

            # Get updated loan data
            loans = await self.client.get_flexible_loan_ongoing_orders()
            current_loan = None
            for l in loans:
                if l.get('collateralCoin') == coll_coin and l.get('loanCoin') == loan_coin:
                    current_loan = l
                    break

            if not current_loan:
                result['errors'].append('Position no longer exists')
                break

            current_leverage = await self.get_current_leverage(current_loan)

            # Check if borrow amount is sufficient
            _, borrow_usd = await self.calculate_borrow_amount(current_loan)
            if borrow_usd < MIN_LOOP_USD:
                logger.info(f"  Borrow amount ${borrow_usd:.2f} too small - position maxed out at {current_leverage:.2f}x")
                result['final_leverage'] = current_leverage
                break

            # Execute loop
            logger.info(f"  Loop {loop_count}: leverage {current_leverage:.2f}x, can borrow ${borrow_usd:.2f}")
            loop_result = await self.execute_loop(current_loan)

            if loop_result['success']:
                result['loops_executed'] += 1
                result['total_borrowed_usd'] += loop_result['borrowed_usd']
                result['total_added_usd'] += loop_result['added_usd']
            else:
                result['errors'].append(loop_result['error'])
                break

            await asyncio.sleep(API_DELAY)

        # Get final leverage
        loans = await self.client.get_flexible_loan_ongoing_orders()
        for l in loans:
            if l.get('collateralCoin') == coll_coin and l.get('loanCoin') == loan_coin:
                result['final_leverage'] = await self.get_current_leverage(l)
                break

        logger.info(f"{position_name}: {initial_leverage:.2f}x -> {result['final_leverage']:.2f}x ({result['loops_executed']} loops)")

        return result

    async def loop_all_positions(self) -> Dict:
        """
        Loop all active positions to MAXIMIZE leverage (no cap)
        Then sweep remaining spot balances into collateral
        Keep only $10 reserve
        """
        loans = await self.client.get_flexible_loan_ongoing_orders()

        if not loans:
            logger.info("No active loan positions to loop")
            return {
                'positions_processed': 0,
                'positions_looped': 0,
                'total_loops': 0,
                'total_borrowed_usd': 0.0,
                'total_added_usd': 0.0,
                'swept_usd': 0.0,
                'details': []
            }

        # Check spot balance first
        spot_value = await self.get_total_spot_value()
        logger.info(f"=== LEVERAGE LOOP: {len(loans)} positions, ${spot_value:.2f} in spot ===")

        result = {
            'positions_processed': len(loans),
            'positions_looped': 0,
            'total_loops': 0,
            'total_borrowed_usd': 0.0,
            'total_added_usd': 0.0,
            'swept_usd': 0.0,
            'details': []
        }

        # Sort by leverage ascending (lowest leverage first)
        sorted_loans = []
        for loan in loans:
            leverage = await self.get_current_leverage(loan)
            sorted_loans.append((leverage, loan))

        sorted_loans.sort(key=lambda x: x[0])

        # Loop each position to max
        for leverage, loan in sorted_loans:
            loop_result = await self.loop_position(loan)
            result['details'].append(loop_result)

            if loop_result['loops_executed'] > 0:
                result['positions_looped'] += 1
                result['total_loops'] += loop_result['loops_executed']
                result['total_borrowed_usd'] += loop_result['total_borrowed_usd']
                result['total_added_usd'] += loop_result['total_added_usd']

            await asyncio.sleep(API_DELAY)

        # SWEEP: Add remaining spot balances to collateral (keep $10 reserve)
        logger.info("=== SWEEPING SPOT BALANCES ===")
        loans = await self.client.get_flexible_loan_ongoing_orders()  # Refresh
        sweep_result = await self.sweep_spot_to_collateral(loans)
        result['swept_usd'] = sweep_result['swept_usd']

        # Final spot check
        final_spot = await self.get_total_spot_value()

        logger.info(f"=== LEVERAGE LOOP COMPLETE ===")
        logger.info(f"  Positions looped: {result['positions_looped']}/{result['positions_processed']}")
        logger.info(f"  Total loops: {result['total_loops']}")
        logger.info(f"  Total borrowed: ${result['total_borrowed_usd']:.2f}")
        logger.info(f"  Total added: ${result['total_added_usd']:.2f}")
        logger.info(f"  Swept from spot: ${result['swept_usd']:.2f}")
        logger.info(f"  Remaining spot: ${final_spot:.2f}")

        return result
