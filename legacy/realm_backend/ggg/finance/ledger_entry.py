from typing import Any, Dict, List, Optional

from ic_python_db import (
    Entity,
    Integer,
    ManyToOne,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.ledger_entry")


class EntryType:
    """Ledger entry classification for financial statements."""
    # Balance Sheet
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    # Income Statement
    REVENUE = "revenue"
    EXPENSE = "expense"


class Category:
    """Standard categories for ledger entries."""
    # Revenues
    TAX = "tax"
    FEE = "fee"
    GRANT = "grant"
    FINE = "fine"
    SERVICE = "service"
    INVESTMENT_INCOME = "investment_income"
    INTERGOVERNMENTAL = "intergovernmental"
    UNALLOCATED = "unallocated"  # swept deposits pending classification

    # Interfund movements (booked as equity so they never distort the
    # income statement; see core.treasury_allocation, issue #261)
    TRANSFER_IN = "transfer_in"

    # Expenses
    PERSONNEL = "personnel"
    SUPPLIES = "supplies"
    SERVICES = "services"
    CAPITAL = "capital"
    DEBT = "debt"
    TRANSFER_OUT = "transfer_out"
    
    # Assets
    CASH = "cash"
    RECEIVABLE = "receivable"
    PROPERTY = "property"
    EQUIPMENT = "equipment"
    INVENTORY = "inventory"
    
    # Liabilities
    PAYABLE = "payable"
    BOND = "bond"
    LOAN = "loan"
    DEFERRED_REVENUE = "deferred_revenue"
    
    # Equity/Fund Balance
    FUND_BALANCE = "fund_balance"
    RETAINED_EARNINGS = "retained_earnings"


class LedgerEntry(Entity, TimestampedMixin):
    """
    Ledger Entry - GGG Government Accounting Standard.
    
    Universal double-entry ledger line. Multiple entries share a
    transaction_id where sum(debit) must equal sum(credit).
    
    This single entity supports all three financial statements:
    - Balance Sheet: entry_type in (asset, liability, equity)
    - Income Statement: entry_type in (revenue, expense)
    - Cash Flow: entries linked to Transfer, grouped by category
    
    Double-entry rules:
    - Assets increase with debit, decrease with credit
    - Liabilities increase with credit, decrease with debit
    - Equity increases with credit, decreases with debit
    - Revenue increases with credit
    - Expenses increase with debit
    """
    __alias__ = "id"
    id = String(max_length=64)
    
    # Double-entry grouping
    transaction_id = String(max_length=64)  # Groups debit/credit pairs
    
    # Classification
    entry_type = String(max_length=32)  # asset, liability, equity, revenue, expense
    category = String(max_length=64)    # tax, personnel, cash, payable, etc.
    
    # Double-entry amounts (one is typically 0)
    debit = Integer(default=0)
    credit = Integer(default=0)
    
    # Time & Organization
    entry_date = String(max_length=32)  # ISO format
    fund = ManyToOne("Fund", "ledger_entries")
    fiscal_period = ManyToOne("FiscalPeriod", "ledger_entries")
    
    # Links to existing GGG entities (all optional)
    transfer = ManyToOne("Transfer", "ledger_entries")
    invoice = ManyToOne("Invoice", "ledger_entries")
    user = ManyToOne("User", "ledger_entries")
    organization = ManyToOne("Organization", "ledger_entries")
    contract = ManyToOne("Contract", "ledger_entries")
    
    # Justice System links
    case = ManyToOne("Case", "ledger_entries")
    penalty = ManyToOne("Penalty", "ledger_entries")
    appeal = ManyToOne("Appeal", "ledger_entries")
    
    # Currency denomination
    currency = String(max_length=16)  # Currency symbol for this entry (e.g., "ckUSDC")
    
    # Metadata
    description = String(max_length=512)
    reference = String(max_length=128)  # External reference number
    tags = String(max_length=256)       # Flexible tagging

    def __repr__(self):
        amt = f"D{self.debit}" if self.debit else f"C{self.credit}"
        return f"LedgerEntry(id={self.id!r}, {self.entry_type}/{self.category}, {amt})"

    def amount(self) -> int:
        """Return the non-zero amount (debit or credit)."""
        return self.debit if self.debit else self.credit

    def is_debit(self) -> bool:
        """Check if this is a debit entry."""
        return (self.debit or 0) > 0

    def is_credit(self) -> bool:
        """Check if this is a credit entry."""
        return (self.credit or 0) > 0

    @classmethod
    def validate_transaction(cls, transaction_id: str) -> bool:
        """
        Validate that a transaction is balanced (debits = credits).
        Returns True if balanced, False otherwise.
        """
        entries = cls.find({"transaction_id": transaction_id})
        total_debit = sum(e.debit or 0 for e in entries)
        total_credit = sum(e.credit or 0 for e in entries)
        return total_debit == total_credit

    @classmethod
    def create_transaction(
        cls,
        transaction_id: str,
        entries: List[dict],
        validate: bool = True
    ) -> List["LedgerEntry"]:
        """
        Create a balanced double-entry transaction.
        
        Args:
            transaction_id: Unique identifier for this transaction
            entries: List of entry dicts with keys: entry_type, category, 
                     debit/credit, fund, fiscal_period, etc.
            validate: If True, raises ValueError if debits != credits
            
        Returns:
            List of created LedgerEntry instances
            
        Raises:
            ValueError: If transaction is unbalanced and validate=True
        """
        total_debit = sum(e.get("debit", 0) for e in entries)
        total_credit = sum(e.get("credit", 0) for e in entries)
        
        if validate and total_debit != total_credit:
            raise ValueError(
                f"Unbalanced transaction {transaction_id}: "
                f"debit={total_debit}, credit={total_credit}"
            )
        
        created = []
        for i, entry_data in enumerate(entries):
            entry_id = f"{transaction_id}_{i}"
            entry = cls(
                id=entry_id,
                transaction_id=transaction_id,
                **entry_data
            )
            created.append(entry)
            logger.info(f"Created ledger entry {entry_id}")
        
        return created

    @classmethod
    def get_balance(cls, entry_type: str, category: str = None, fund=None) -> int:
        """
        Calculate net balance for an entry type (and optionally category/fund).
        
        For assets/expenses: balance = sum(debit) - sum(credit)
        For liabilities/equity/revenue: balance = sum(credit) - sum(debit)
        """
        filters = {"entry_type": entry_type}
        if category:
            filters["category"] = category
        if fund:
            filters["fund"] = fund
            
        entries = cls.find(filters)
        total_debit = sum(e.debit or 0 for e in entries)
        total_credit = sum(e.credit or 0 for e in entries)
        
        # Normal balance depends on entry type
        if entry_type in (EntryType.ASSET, EntryType.EXPENSE):
            return total_debit - total_credit
        else:  # liability, equity, revenue
            return total_credit - total_debit

    @classmethod
    def get_balance_sheet(
        cls,
        fund: Optional[Any] = None,
        fiscal_period: Optional[Any] = None,
        as_of_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate Balance Sheet (Statement of Net Position).
        
        Args:
            fund: Optional Fund to filter by
            fiscal_period: Optional FiscalPeriod to filter by
            as_of_date: Optional date string to filter entries up to
            
        Returns:
            Dict with assets, liabilities, fund_balance, and totals
        """
        def get_entries(entry_type: str) -> List["LedgerEntry"]:
            filters = {"entry_type": entry_type}
            entries = cls.find(filters)
            if fund:
                entries = [e for e in entries if e.fund == fund]
            if fiscal_period:
                entries = [e for e in entries if e.fiscal_period == fiscal_period]
            if as_of_date:
                entries = [e for e in entries if e.entry_date and e.entry_date <= as_of_date]
            return entries
        
        def calc_balance(entries: List["LedgerEntry"], normal_debit: bool) -> int:
            total_debit = sum(e.debit or 0 for e in entries)
            total_credit = sum(e.credit or 0 for e in entries)
            return total_debit - total_credit if normal_debit else total_credit - total_debit
        
        def by_category(entries: List["LedgerEntry"], normal_debit: bool) -> Dict[str, int]:
            categories = {}
            for entry in entries:
                cat = entry.category or "uncategorized"
                if cat not in categories:
                    categories[cat] = {"entries": []}
                categories[cat]["entries"].append(entry)
            return {cat: calc_balance(data["entries"], normal_debit) 
                    for cat, data in categories.items()}
        
        # Get entries by type
        asset_entries = get_entries(EntryType.ASSET)
        liability_entries = get_entries(EntryType.LIABILITY)
        equity_entries = get_entries(EntryType.EQUITY)
        
        # Calculate by category
        assets = by_category(asset_entries, normal_debit=True)
        liabilities = by_category(liability_entries, normal_debit=False)
        fund_balance = by_category(equity_entries, normal_debit=False)
        
        total_assets = sum(assets.values())
        total_liabilities = sum(liabilities.values())
        total_fund_balance = sum(fund_balance.values())
        
        return {
            "title": "Balance Sheet (Statement of Net Position)",
            "assets": {
                "items": assets,
                "total": total_assets
            },
            "liabilities": {
                "items": liabilities,
                "total": total_liabilities
            },
            "fund_balance": {
                "items": fund_balance,
                "total": total_fund_balance
            },
            "total_liabilities_and_fund_balance": total_liabilities + total_fund_balance,
            "is_balanced": total_assets == (total_liabilities + total_fund_balance),
            "net_position": total_assets - total_liabilities
        }

    @classmethod
    def get_income_statement(
        cls,
        fiscal_period: Optional[Any] = None,
        fund: Optional[Any] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate Income Statement (Statement of Activities).
        
        Args:
            fiscal_period: Optional FiscalPeriod to filter by
            fund: Optional Fund to filter by
            start_date: Optional start date for date range
            end_date: Optional end date for date range
            
        Returns:
            Dict with revenues, expenses, and net income/loss
        """
        def get_entries(entry_type: str) -> List["LedgerEntry"]:
            filters = {"entry_type": entry_type}
            entries = cls.find(filters)
            if fund:
                entries = [e for e in entries if e.fund == fund]
            if fiscal_period:
                entries = [e for e in entries if e.fiscal_period == fiscal_period]
            if start_date:
                entries = [e for e in entries if e.entry_date and e.entry_date >= start_date]
            if end_date:
                entries = [e for e in entries if e.entry_date and e.entry_date <= end_date]
            return entries
        
        def by_category(entries: List["LedgerEntry"], normal_debit: bool) -> Dict[str, int]:
            categories = {}
            for entry in entries:
                cat = entry.category or "uncategorized"
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(entry)
            
            result = {}
            for cat, cat_entries in categories.items():
                total_debit = sum(e.debit or 0 for e in cat_entries)
                total_credit = sum(e.credit or 0 for e in cat_entries)
                result[cat] = total_debit - total_credit if normal_debit else total_credit - total_debit
            return result
        
        # Get entries
        revenue_entries = get_entries(EntryType.REVENUE)
        expense_entries = get_entries(EntryType.EXPENSE)
        
        # Calculate by category
        revenues = by_category(revenue_entries, normal_debit=False)  # Revenue normal is credit
        expenses = by_category(expense_entries, normal_debit=True)   # Expense normal is debit
        
        total_revenues = sum(revenues.values())
        total_expenses = sum(expenses.values())
        net_income = total_revenues - total_expenses
        
        return {
            "title": "Income Statement (Statement of Activities)",
            "revenues": {
                "items": revenues,
                "total": total_revenues
            },
            "expenses": {
                "items": expenses,
                "total": total_expenses
            },
            "net_income": net_income,
            "surplus_or_deficit": "surplus" if net_income >= 0 else "deficit"
        }

    @classmethod
    def get_cash_flow_statement(
        cls,
        fiscal_period: Optional[Any] = None,
        fund: Optional[Any] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate Cash Flow Statement.
        
        Classifies cash flows into:
        - Operating: Day-to-day operations (taxes, fees, salaries, supplies)
        - Investing: Capital assets (property, equipment purchases/sales)
        - Financing: Debt (bonds, loans issued/repaid)
        
        Args:
            fiscal_period: Optional FiscalPeriod to filter by
            fund: Optional Fund to filter by
            start_date: Optional start date
            end_date: Optional end date
            
        Returns:
            Dict with cash flows by activity type
        """
        # Get all cash-related entries
        cash_entries = cls.find({"category": Category.CASH})
        
        if fund:
            cash_entries = [e for e in cash_entries if e.fund == fund]
        if fiscal_period:
            cash_entries = [e for e in cash_entries if e.fiscal_period == fiscal_period]
        if start_date:
            cash_entries = [e for e in cash_entries if e.entry_date and e.entry_date >= start_date]
        if end_date:
            cash_entries = [e for e in cash_entries if e.entry_date and e.entry_date <= end_date]
        
        # Classify by tags or linked entities
        operating = {"items": {}, "total": 0}
        investing = {"items": {}, "total": 0}
        financing = {"items": {}, "total": 0}
        
        # Categories that indicate investing activities
        investing_categories = {Category.PROPERTY, Category.EQUIPMENT, Category.CAPITAL}
        # Categories that indicate financing activities  
        financing_categories = {Category.BOND, Category.LOAN}
        
        for entry in cash_entries:
            # Net cash effect: debit increases cash, credit decreases
            net = (entry.debit or 0) - (entry.credit or 0)
            desc = entry.description or entry.tags or "other"
            
            # Classify based on tags or linked entities
            tags = (entry.tags or "").lower()
            
            if "investing" in tags or entry.contract:
                # Linked to contract = capital project = investing
                if desc not in investing["items"]:
                    investing["items"][desc] = 0
                investing["items"][desc] += net
                investing["total"] += net
            elif "financing" in tags or any(fc in tags for fc in ["bond", "loan", "debt"]):
                if desc not in financing["items"]:
                    financing["items"][desc] = 0
                financing["items"][desc] += net
                financing["total"] += net
            else:
                # Default to operating
                if desc not in operating["items"]:
                    operating["items"][desc] = 0
                operating["items"][desc] += net
                operating["total"] += net
        
        net_change = operating["total"] + investing["total"] + financing["total"]
        
        # Get beginning cash balance (sum of all prior cash entries)
        all_cash = cls.find({"category": Category.CASH})
        if start_date:
            prior_cash = [e for e in all_cash if e.entry_date and e.entry_date < start_date]
        else:
            prior_cash = []
        beginning_cash = sum((e.debit or 0) - (e.credit or 0) for e in prior_cash)
        
        return {
            "title": "Cash Flow Statement",
            "operating_activities": operating,
            "investing_activities": investing,
            "financing_activities": financing,
            "net_change_in_cash": net_change,
            "beginning_cash_balance": beginning_cash,
            "ending_cash_balance": beginning_cash + net_change
        }
