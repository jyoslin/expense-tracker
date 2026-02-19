import streamlit as st
import hmac
from supabase import create_client, Client
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
import re

# --- 1. SECURITY & SETUP ---
st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

def check_password():
    # 1. Check if the secret token is in the URL
    if st.query_params.get("token") == st.secrets["APP_PASSWORD"]:
        # Log the user in
        st.session_state["password_correct"] = True
        
        # UPGRADE: Immediately erase the token from the URL!
        st.query_params.clear()
        
    # 2. Fallback: Standard password box if URL token is missing
    def password_entered():
        if hmac.compare_digest(st.session_state["password"], st.secrets["APP_PASSWORD"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    # 3. If already logged in, show the app
    if st.session_state.get("password_correct", False):
        return True

    # 4. Otherwise, show the password input
    st.text_input("🔒 Please enter your password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("😕 Password incorrect")
    return False

if not check_password():
    st.stop()
# Connect to Supabase
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# --- 2. HELPER FUNCTIONS ---
def clear_cache():
    st.cache_data.clear()

@st.cache_data(ttl=300)
def get_accounts(show_inactive=False):
    accounts = supabase.table('accounts').select("*").execute().data
    df = pd.DataFrame(accounts)
    
    cols = ['id', 'name', 'type', 'balance', 'include_net_worth', 'is_liquid_asset', 
            'goal_amount', 'goal_date', 'sort_order', 'is_active', 'remark', 
            'currency', 'manual_exchange_rate']
            
    if df.empty: return pd.DataFrame(columns=cols)
    
    defaults = {
        'sort_order': 99, 'is_active': True, 'remark': "", 
        'currency': "SGD", 'manual_exchange_rate': 1.0, 
        'include_net_worth': True, 'is_liquid_asset': True,
        'goal_amount': 0.0, 'goal_date': None 
    }
    for col, val in defaults.items():
        if col not in df.columns: df[col] = val
        
    df['balance'] = pd.to_numeric(df['balance'], errors='coerce').fillna(0.0)
    df['goal_amount'] = pd.to_numeric(df['goal_amount'], errors='coerce').fillna(0.0)
    df['manual_exchange_rate'] = pd.to_numeric(df['manual_exchange_rate'], errors='coerce').fillna(1.0)
    df['sort_order'] = pd.to_numeric(df['sort_order'], errors='coerce').fillna(99).astype(int)
    
    df['is_active'] = df['is_active'].fillna(True).astype(bool)
    df['include_net_worth'] = df['include_net_worth'].fillna(True).astype(bool)
    df['is_liquid_asset'] = df['is_liquid_asset'].fillna(True).astype(bool)
    df['goal_date'] = pd.to_datetime(df['goal_date'], errors='coerce').dt.date
    
    if not show_inactive:
        df = df[df['is_active'] == True]
        
    type_order = ['Bank', 'Credit Card', 'Custodial', 'Loan', 'Sinking Fund', 'Investment']
    df['type'] = pd.Categorical(df['type'], categories=type_order, ordered=True)
    
    return df.sort_values(by=['type', 'sort_order', 'name']).reset_index(drop=True)

@st.cache_data(ttl=3600)
def get_categories(type_filter=None):
    query = supabase.table('categories').select("*")
    if type_filter:
        query = query.eq('type', type_filter)
    data = query.execute().data
    if not data: return pd.DataFrame(columns=['id', 'name', 'type', 'budget_limit'])
    
    df = pd.DataFrame(data)
    if 'budget_limit' not in df.columns: 
        df['budget_limit'] = 0.0
    
    df['budget_limit'] = pd.to_numeric(df['budget_limit'], errors='coerce').fillna(0.0)
    return df.sort_values('name').reset_index(drop=True)

def update_balance(account_id, amount_change):
    if not account_id: return 
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def apply_editor_changes(table_name, original_df, editor_key):
    changes = st.session_state[editor_key]
    
    for idx in changes.get("deleted_rows", []):
        row_id = original_df.iloc[idx]['id']
        supabase.table(table_name).delete().eq('id', row_id).execute()
        
    for idx_str, edits in changes.get("edited_rows", {}).items():
        idx = int(idx_str)
        row_id = original_df.iloc[idx]['id']
        
        edits.pop('_index', None)
        if 'goal_date' in edits:
            edits['goal_date'] = str(edits['goal_date']) if edits['goal_date'] else None
            
        if edits:
            supabase.table(table_name).update(edits).eq('id', row_id).execute()
        
    for new_row in changes.get("added_rows", []):
        new_row.pop('_index', None) 
        new_row.pop('id', None) 
        if not new_row.get('name') or str(new_row.get('name')).strip() == "":
            continue 
            
        if table_name == 'accounts':
            new_row.setdefault('balance', 0.0)
            new_row.setdefault('currency', 'SGD')
            new_row.setdefault('manual_exchange_rate', 1.0)
            new_row.setdefault('is_active', True)
            new_row.setdefault('sort_order', 99)
            new_row.setdefault('include_net_worth', True)
            new_row.setdefault('is_liquid_asset', True)
            new_row.setdefault('goal_amount', 0.0)
            new_row.setdefault('type', 'Bank')
            if 'goal_date' in new_row:
                new_row['goal_date'] = str(new_row['goal_date']) if new_row['goal_date'] else None
        elif table_name == 'categories':
            new_row.setdefault('budget_limit', 0.0)
            new_row.setdefault('type', 'Expense')
            
        supabase.table(table_name).insert(new_row).execute()
    clear_cache()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id, category, remark):
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id, "category": category,
        "remark": remark
    }).execute()

    if type in ["Expense", "Virtual Expense"]: update_balance(from_acc_id, -amount)
    elif type in ["Income", "Virtual Funding"]: update_balance(to_acc_id, amount)
    elif type == "Increase Loan": update_balance(to_acc_id, -amount) 
    elif type == "Transfer":
        if from_acc_id: update_balance(from_acc_id, -amount)
        if to_acc_id: update_balance(to_acc_id, amount)
    
    clear_cache()

# UPGRADE: Smart interceptor for Future Routing
def process_transaction(tx_date, amount, description, t_type, from_acc_id, to_acc_id, category, remark):
    if tx_date > date.today():
        desc_with_rem = f"{description} | Note: {remark}" if remark else description
        supabase.table('schedule').insert({
            "description": desc_with_rem,
            "amount": amount,
            "type": t_type,
            "from_account_id": from_acc_id,
            "to_account_id": to_acc_id,
            "frequency": "One-Time",
            "next_run_date": str(tx_date),
            "is_manual": False,
            "category": category
        }).execute()
        return True 
    else:
        add_transaction(tx_date, amount, description, t_type, from_acc_id, to_acc_id, category, remark)
        return False

def delete_transaction(tx_id):
    tx_data = supabase.table('transactions').select("*").eq('id', tx_id).execute().data
    if not tx_data:
        return False
        
    tx = tx_data[0]
    remark = tx.get('remark') or ""
    
    batch_id = None
    if "[Batch:" in remark:
        start = remark.find("[Batch:")
        end = remark.find("]", start)
        if end != -1:
            batch_id = remark[start:end+1]
            
    if batch_id:
        to_delete = supabase.table('transactions').select("*").ilike('remark', f'%{batch_id}%').execute().data
    else:
        to_delete = [tx]
        
    for t in to_delete:
        amt = float(t['amount'])
        t_type = t['type']
        
        if t_type in ["Expense", "Virtual Expense"]: update_balance(t['from_account_id'], amt) 
        elif t_type in ["Income", "Virtual Funding"]: update_balance(t['to_account_id'], -amt) 
        elif t_type == "Increase Loan": update_balance(t['to_account_id'], amt) 
        elif t_type == "Transfer":
            if t['from_account_id']: update_balance(t['from_account_id'], amt)
            if t['to_account_id']: update_balance(t['to_account_id'], -amt)

        supabase.table('transactions').delete().eq('id', t['id']).execute()
        
    clear_cache()
    return True

# --- NEW: BACKGROUND AUTO-FUNDER ENGINE ---
def run_auto_funder():
    today = date.today()
    first_str = today.replace(day=1).strftime("%Y-%m-%d") 
    
    try:
        recent_fundings = supabase.table('transactions').select('to_account_id').eq('type', 'Virtual Funding').gte('date', first_str).execute().data
        funded_account_ids = [tx['to_account_id'] for tx in recent_fundings]
    except Exception:
        funded_account_ids = []
    
    accounts = supabase.table('accounts').select("*").eq('type', 'Sinking Fund').execute().data
    
    for acc in accounts:
        if acc['id'] in funded_account_ids:
            continue
        
        remark = acc.get('remark') or ""
        if '[Auto:True]' not in remark:
            continue
            
        goal_amount = float(acc.get('goal_amount') or 0)
        balance = float(acc.get('balance') or 0)
        
        if goal_amount <= 0 or balance >= goal_amount:
            continue
            
        term_match = re.search(r'\[Term:(\d+)\]', remark)
        term = int(term_match.group(1)) if term_match else 12
        term = max(1, term) 
        
        monthly_contrib = goal_amount / term
        
        if balance + monthly_contrib > goal_amount:
            monthly_contrib = goal_amount - balance
        
        if monthly_contrib > 0:
            add_transaction(
                date=today, 
                amount=monthly_contrib, 
                description=f"Auto Monthly Funding: {acc['name']}", 
                type="Virtual Funding", 
                from_acc_id=None, 
                to_acc_id=acc['id'], 
                category="Fund", 
                remark="Automated envelope funding"
            )

if 'auto_funded' not in st.session_state:
    run_auto_funder()
    st.session_state['auto_funded'] = True


# --- 3. APP START ---
st.title("💰 My Wealth Manager")
df_active = get_accounts(show_inactive=False)

account_map = dict(zip(df_active['name'], df_active['id']))
balance_map = dict(zip(df_active['name'], df_active['balance']))
type_map = dict(zip(df_active['name'], df_active['type'])) # NEW: Map names to types for the icons
account_list = df_active['name'].tolist() if not df_active.empty else []

non_loan_accounts = df_active[df_active['type'] != 'Loan']['name'].tolist() if not df_active.empty else []
expense_src_accounts = df_active[~df_active['type'].isin(['Loan', 'Custodial', 'Sinking Fund'])]['name'].tolist() if not df_active.empty else []

# --- SIDEBAR NAVIGATION & ICON MAPPING ---
st.sidebar.title("Navigation")
menu = st.sidebar.radio(
    "Go to:", 
    ["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings", "📈 Reports"], 
    index=1
)

st.sidebar.divider()

# 1. Put everything inside an expander so it is hidden by default
with st.sidebar.expander("🎨 Icon Mapping", expanded=False):
    st.caption("Customize account & transaction icons:")
    
    # 2. Use columns to put 3 icons per row
    col1, col2, col3 = st.columns(3)
    
    with col1:
        icon_bank = st.text_input("Bank", "🏦")
        icon_sf = st.text_input("Sinking", "🎯")
        icon_inc = st.text_input("Income", "🟢")
        
    with col2:
        icon_cc = st.text_input("Card", "💳")
        icon_loan = st.text_input("Loan", "📉")
        icon_exp = st.text_input("Expense", "🔴")
        
    with col3:
        icon_cust = st.text_input("Custodial", "🛡️")
        icon_inv = st.text_input("Invest", "📈")
        icon_tx = st.text_input("Transfer", "🔄")

icon_map = {
    "Bank": icon_bank, "Credit Card": icon_cc, "Custodial": icon_cust,
    "Sinking Fund": icon_sf, "Loan": icon_loan, "Investment": icon_inv
}

tx_icon_map = {
    "Income": icon_inc, "Virtual Funding": icon_inc, 
    "Expense": icon_exp, "Virtual Expense": icon_exp, 
    "Sinking Fund Expense": icon_exp, "Custodial Expense": icon_exp, 
    "Transfer": icon_tx, "Increase Loan": "📉"
}

# UPGRADE: Formats dropdowns to display icon + name + balance, but behind the scenes returns pure name!
def format_acc(acc_name):
    if not acc_name: return "Select Account..."
    bal = balance_map.get(acc_name, 0)
    acc_type = type_map.get(acc_name, "Bank")
    icon = icon_map.get(acc_type, "🏦")
    return f"{icon} {acc_name} (Bal: ${bal:,.2f})"

# --- MENU: OVERVIEW ---
if menu == "📊 Overview":
    st.header("📊 Overview")
    
    if not df_active.empty:
        # UPGRADE: Filter the dataframe to ONLY include SGD accounts
        df_calc = df_active[df_active['currency'] == 'SGD'].copy()
        
        # 1. Calculate individual category totals using the raw 'balance'
        bank_tot = df_calc[df_calc['type'] == 'Bank']['balance'].sum()
        cc_tot = df_calc[df_calc['type'] == 'Credit Card']['balance'].sum()
        custodial_tot = df_calc[df_calc['type'] == 'Custodial']['balance'].sum()
        sf_tot = df_calc[df_calc['type'] == 'Sinking Fund']['balance'].sum()
        
        # 2. Calculate combined metrics based on custom formulas
        net_worth = bank_tot - cc_tot - custodial_tot + sf_tot
        liquid = bank_tot - cc_tot - custodial_tot - sf_tot
        
    else:
        bank_tot = cc_tot = custodial_tot = sf_tot = 0
        net_worth = liquid = 0
    
    # --- UI DISPLAY ---
    
    # Row 1: The Four Base Totals
    st.subheader("🏦 SGD Account Totals")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bank Total", f"${bank_tot:,.2f}")
    c2.metric("Credit Card Total", f"${cc_tot:,.2f}")
    c3.metric("Custodial Total", f"${custodial_tot:,.2f}")
    c4.metric("Sinking Fund Total", f"${sf_tot:,.2f}")
    
    st.divider()
    
    # Row 2: The Calculated Metrics & Formulas
    st.subheader("📈 Wealth Metrics (SGD Only)")
    m1, m2 = st.columns(2)
    
    # Dynamically build the formula strings to show the live math
    nw_formula = f"Bank (${bank_tot:,.2f}) - CC (${cc_tot:,.2f}) - Custodial (${custodial_tot:,.2f}) + Sinking Fund (${sf_tot:,.2f})"
    la_formula = f"Bank (${bank_tot:,.2f}) - CC (${cc_tot:,.2f}) - Custodial (${custodial_tot:,.2f}) - Sinking Fund (${sf_tot:,.2f})"
    
    with m1:
        st.metric("Total Net Worth", f"${net_worth:,.2f}")
        st.caption(f"**Calculation:** {nw_formula}")
        
    with m2:
        st.metric("Liquid Assets", f"${liquid:,.2f}")
        st.caption(f"**Calculation:** {la_formula}")
    
    st.divider()
    st.subheader("💳 Current Balances")
    if not df_active.empty:
        # 1. Prepare the display data
        df_display = df_active[['name', 'type', 'balance', 'currency']].copy()
        
        # Add icons to name
        df_display['name'] = df_display.apply(lambda row: f"{icon_map.get(row['type'], '')} {row['name']}", axis=1)

        # 2. Render clickable table
        # We use format="%.2f" (No '$') to respect the user's currency column
        event = st.dataframe(
            df_display, 
            hide_index=True, 
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "name": "Account Name",
                "type": "Type",
                "balance": st.column_config.NumberColumn("Balance", format="%.2f"), # No currency symbol forced
                "currency": "Currency"
            }
        )
        
        # 3. Handle Click Event: Update the 'ledger_select' for the Account Statement below
        if event.selection.rows:
            selected_idx = event.selection.rows[0]
            # Extract the pure account name (removing the icon we added for display)
            # We assume the raw name is what matches the selectbox options
            full_display_name = df_display.iloc[selected_idx]['name']
            
            # Helper to find the matching original name in your account_list
            # (Matches purely based on the string if icons are consistent, or we fallback)
            # Since we added icons to df_display, let's find the original name from df_active
            selected_real_name = df_active.iloc[selected_idx]['name']
            
            # Update the session state widget for the dropdown below
            st.session_state["ledger_select"] = selected_real_name

    st.divider()

    st.subheader("📜 Account Statement (With Forward Projections)")
    selected_acc_name = st.selectbox("Select Account to View Statement:", account_list, key="ledger_select", format_func=format_acc)
    
    if selected_acc_name:
        sel_id = account_map[selected_acc_name]
        current_bal = balance_map.get(selected_acc_name, 0.0)
        id_to_name = {v: k for k, v in account_map.items()} 
        
        st.metric(f"Current Balance for {selected_acc_name}", f"${current_bal:,.2f}")
        
        # 1. Fetch Future Scheduled Transactions
        sched_data = supabase.table('schedule').select("*") \
            .or_(f"from_account_id.eq.{sel_id},to_account_id.eq.{sel_id}") \
            .gte("next_run_date", str(date.today() + timedelta(days=1))) \
            .order("next_run_date", desc=False).execute().data

        future_view = []
        temp_bal_fwd = current_bal
        
        for row in sched_data:
            is_inflow = row['to_account_id'] == sel_id
            amt = float(row['amount'])
            
            if row['type'] == 'Transfer':
                impact = amt if is_inflow else -amt
                other_acc_id = row['from_account_id'] if is_inflow else row['to_account_id']
                other_acc_name = id_to_name.get(other_acc_id, "")
                linked_info = f"From: {other_acc_name}" if is_inflow else f"To: {other_acc_name}"
            else:
                impact = -amt if row['type'] in ['Expense', 'Virtual Expense', 'Increase Loan'] else amt
                linked_info = ""

            temp_bal_fwd += impact
            desc = f"📅 PENDING: {row['description']}"
            
            future_view.append({
                "ID": f"S-{row['id']}", 
                "Date": row['next_run_date'], 
                "Description": desc, 
                "Amount": impact, 
                "Running Balance": temp_bal_fwd,
                "Category": row['category'], 
                "Type": row['type'],
                "Linked Account / Notes": linked_info
            })
            
        future_view.reverse() # Put furthest dates at the top to stack down into 'today'

        # 2. Fetch Past Transactions
        txs = supabase.table('transactions').select("*") \
            .or_(f"from_account_id.eq.{sel_id},to_account_id.eq.{sel_id}") \
            .order("date", desc=True).order("id", desc=True).limit(50).execute().data
            
        past_view = []
        temp_bal_bwd = current_bal
        
        for row in txs:
            is_inflow = row['to_account_id'] == sel_id
            amt = float(row['amount'])
            
            if row['type'] == 'Transfer':
                display_amt = amt if is_inflow else -amt
                other_acc_id = row['from_account_id'] if is_inflow else row['to_account_id']
                other_acc_name = id_to_name.get(other_acc_id, "Unknown Account")
                linked_info = f"From: {other_acc_name}" if is_inflow else f"To: {other_acc_name}"
            else:
                display_amt = -amt if row['type'] in ['Expense', 'Virtual Expense', 'Increase Loan'] else amt
                raw_remark = row.get('remark') or ""
                linked_info = raw_remark.split(' [Batch:')[0].strip()
            
            past_view.append({
                "ID": str(row['id']), 
                "Date": row['date'], 
                "Description": row['description'], 
                "Amount": display_amt, 
                "Running Balance": temp_bal_bwd,
                "Category": row['category'], 
                "Type": row['type'],
                "Linked Account / Notes": linked_info
            })
            
            temp_bal_bwd -= display_amt # Peel off this transaction to find balance BEFORE it happened
            
        # Combine Future + Past
        view_data = future_view + past_view
        
        if view_data:
            df_view = pd.DataFrame(view_data)
            
            # Apply the mapped icons to the 'Type' column strings
            df_view['Type'] = df_view['Type'].apply(lambda t: f"{tx_icon_map.get(t, '')} {t}")
            
            # UPGRADE: Interactive Selectable Dataframe remains fully intact!
            event = st.dataframe(
                df_view, 
                use_container_width=True, 
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "Amount": st.column_config.NumberColumn("Amount", format="%.2f"),
                    "Running Balance": st.column_config.NumberColumn("Running Balance", format="$%.2f")
                }
            )
            
            st.divider()
            
            st.write("### 🗑️ Delete / Reverse Transaction")
            
            # UPGRADE: Click-to-Select Safety Preview Logic
            if len(event.selection.rows) > 0:
                selected_idx = event.selection.rows[0]
                selected_row = df_view.iloc[selected_idx]
                raw_id = str(selected_row["ID"])
                
                with st.container(border=True):
                    st.warning("⚠️ **Deletion Preview & Impact Analysis**")
                    
                    # Logic for clicking a FUTURE Scheduled Payment
                    if raw_id.startswith("S-"):
                        real_id = int(raw_id.replace("S-", ""))
                        st.write(f"**Target:** Scheduled Payment - {selected_row['Description']}")
                        st.write(f"**Date:** {selected_row['Date']}")
                        st.write("**Impact:** No current balances will be affected. This future payment will simply be cancelled.")
                        
                        if st.button("🚨 Confirm Cancel Scheduled Payment", use_container_width=True):
                            supabase.table('schedule').delete().eq('id', real_id).execute()
                            st.success("Scheduled payment cancelled!")
                            clear_cache()
                            st.rerun()
                            
                    # Logic for clicking a PAST Transaction
                    else:
                        real_id = int(raw_id)
                        st.write(f"**Target:** Past Transaction - {selected_row['Description']}")
                        st.write(f"**Date:** {selected_row['Date']}")
                        
                        # Fetch the exact details of the targeted transaction
                        tx_data_res = supabase.table('transactions').select("*").eq('id', real_id).execute().data
                        if tx_data_res:
                            target_tx = tx_data_res[0]
                            remark = target_tx.get('remark') or ""
                            
                            # Check if it's a batched transaction
                            batch_id = None
                            if "[Batch:" in remark:
                                start = remark.find("[Batch:")
                                end = remark.find("]", start)
                                if end != -1:
                                    batch_id = remark[start:end+1]
                            
                            id_to_name = {v: k for k, v in account_map.items()}
                            
                            if batch_id:
                                st.write("**Impact:** This is a batched transaction. Deleting it will reverse multiple entries:")
                                batched_txs = supabase.table('transactions').select("*").ilike('remark', f'%{batch_id}%').execute().data
                                
                                for b_tx in batched_txs:
                                    b_amt = float(b_tx['amount'])
                                    b_type = b_tx['type']
                                    f_acc = id_to_name.get(b_tx['from_account_id'], "Unknown Account")
                                    t_acc = id_to_name.get(b_tx['to_account_id'], "Unknown Account")
                                    
                                    if b_type in ["Expense", "Virtual Expense"]:
                                        st.write(f"* 🟢 **${b_amt:,.2f}** will be **added back** to {f_acc}.")
                                    elif b_type in ["Income", "Virtual Funding"]:
                                        st.write(f"* 🔴 **${b_amt:,.2f}** will be **deducted** from {t_acc}.")
                                    elif b_type == "Transfer":
                                        st.write(f"* 🔄 **${b_amt:,.2f}** will be **returned** from {t_acc} to {f_acc}.")
                            else:
                                t_type = target_tx['type']
                                amt = float(target_tx['amount'])
                                f_acc = id_to_name.get(target_tx['from_account_id'], "Unknown Account")
                                t_acc = id_to_name.get(target_tx['to_account_id'], "Unknown Account")
                                
                                if t_type in ["Expense", "Virtual Expense"]:
                                    st.write(f"**Impact:** 🟢 **${amt:,.2f}** will be **added back** to {f_acc}.")
                                elif t_type in ["Income", "Virtual Funding"]:
                                    st.write(f"**Impact:** 🔴 **${amt:,.2f}** will be **deducted** from {t_acc}.")
                                elif t_type == "Transfer":
                                    st.write(f"**Impact:** 🔄 **${amt:,.2f}** will be **returned** from {t_acc} to {f_acc}.")
                                elif t_type == "Increase Loan":
                                    st.write(f"**Impact:** 🟢 **${amt:,.2f}** debt will be **removed** from {t_acc}.")
                                else:
                                    st.write(f"**Impact:** The transaction will be erased and **${amt:,.2f}** will be restored to your accounts.")
                                
                        if st.button("🚨 Confirm Delete Transaction", use_container_width=True):
                            delete_transaction(real_id)
                            st.success("Transaction deleted and balances restored!")
                            st.rerun()
            else:
                st.info("👆 **Click on any row** in the table above to view deletion options and impact analysis.")


# --- MENU: ENTRY ---
elif menu == "📝 Entry":
    st.header("📝 New Transaction")
    
    msg_container = st.empty() 
    
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial Expense", "Custodial In", "Increase Loan", "Sinking Fund Expense"], horizontal=True)
    
    is_split = False
    if t_type == "Expense":
        is_split = st.checkbox("🔀 Split Payment (Pay from 2 sources)")
    
    with st.form("entry_form"):
        c1, c2 = st.columns(2)
        tx_date = c1.date_input("Date", datetime.today())
        
        f_acc = t_acc = acc1 = acc2 = cust_acc = bank_acc = sf_acc = category = None
        amt = amt1 = amt2 = amt_bank = 0.0
        
        # UPGRADE: All dropdowns now inject format_acc lambda to show live balances seamlessly!
        if t_type == "Expense" and is_split:
            st.info("Split Payment: Amount 1 + Amount 2 = Total Expense")
            col_a, col_b = st.columns(2)
            with col_a:
                acc1 = st.selectbox("Source 1", expense_src_accounts, index=None, format_func=format_acc)
                amt1 = st.number_input("Amount 1", min_value=0.0, format="%.2f")
            with col_b:
                acc2 = st.selectbox("Source 2", expense_src_accounts, index=None, format_func=format_acc)
                amt2 = st.number_input("Amount 2", min_value=0.0, format="%.2f")
        
        elif t_type == "Expense":
            f_acc = st.selectbox("Paid From", expense_src_accounts, index=None, format_func=format_acc)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type == "Custodial Expense":
            st.warning("🔻 Empties the Virtual Custodial Account, and deducts from Actual Bank Account")
            c_a, c_b = st.columns(2)
            cust_opts = df_active[df_active['type']=='Custodial']['name']
            bank_opts = df_active[df_active['type']=='Bank']['name']
            
            cust_acc = c_a.selectbox("Custodial Account (Virtual)", cust_opts, index=None, format_func=format_acc)
            bank_acc = c_b.selectbox("Paid via Bank (Actual)", bank_opts, index=None, format_func=format_acc)
            
            amt = c1.number_input("Total Custodial Deduction", min_value=0.01)
            amt_bank = c2.number_input("Actual Amount Paid from Bank", min_value=0.00)

        elif t_type == "Custodial In":
            st.info("🔼 Deposits Virtual money to Custodial AND Actual money to Bank")
            c_a, c_b = st.columns(2)
            cust_opts = df_active[df_active['type']=='Custodial']['name']
            bank_opts = df_active[df_active['type']=='Bank']['name']
            
            cust_acc = c_a.selectbox("Custodial Account (Virtual)", cust_opts, index=None, format_func=format_acc)
            bank_acc = c_b.selectbox("Deposit to Bank (Actual)", bank_opts, index=None, format_func=format_acc)
            
            amt = c2.number_input("Total Amount", min_value=0.01)

        elif t_type == "Sinking Fund Expense":
            st.info("🛍️ Pay for your goal using your real Bank money, and empty out the virtual Sinking Fund envelope.")
            c_a, c_b = st.columns(2)
            sf_opts = df_active[df_active['type']=='Sinking Fund']['name']
            bank_opts = df_active[df_active['type']=='Bank']['name']
            
            sf_acc = c_a.selectbox("Deduct from Virtual Envelope", sf_opts, index=None, format_func=format_acc)
            bank_acc = c_b.selectbox("Paid via Bank (Actual)", bank_opts, index=None, format_func=format_acc)
            
            amt = c2.number_input("Total Amount", min_value=0.01)

        elif t_type == "Income":
            t_acc = st.selectbox("Deposit To", account_list, index=None, format_func=format_acc)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type == "Transfer":
            c_a, c_b = st.columns(2)
            f_acc = c_a.selectbox("From", non_loan_accounts, index=None, format_func=format_acc)
            t_acc = c_b.selectbox("To", account_list, index=None, format_func=format_acc)
            amt = c2.number_input("Amount", min_value=0.01)
            
        elif t_type == "Increase Loan":
            loan_opts = df_active[df_active['type'] == 'Loan']['name'].tolist()
            if len(loan_opts) == 0:
                st.warning("No Loan accounts found.")
            else:
                t_acc = st.selectbox("Select Loan Account", loan_opts, index=None, format_func=format_acc)
            amt = c2.number_input("Amount to Add to Loan", min_value=0.01)

        df_cats_full = get_categories()
        cat_options = []
        if not df_cats_full.empty:
            if t_type in ["Income", "Custodial In"]:
                cat_options = df_cats_full[df_cats_full['type'] == 'Income']['name'].tolist()
            elif t_type == "Transfer":
                cat_options = df_cats_full['name'].tolist() 
            else:
                cat_options = df_cats_full[df_cats_full['type'].isin(['Expense', 'Fund'])]['name'].tolist()
                
        category = st.selectbox("Category", cat_options, index=None, placeholder="Select Category (Optional)...")
        
        desc = st.text_input("Description")
        remark = st.text_area("Notes", height=2)
        
        submitted = st.form_submit_button("Submit Transaction")
        
        if submitted:
            if t_type == "Expense" and is_split and (not acc1 or not acc2):
                st.error("❌ Please select both Source accounts.")
            elif t_type == "Expense" and not is_split and not f_acc:
                st.error("❌ Please select a 'Paid From' account.")
            elif t_type in ["Custodial Expense", "Custodial In"] and (not cust_acc or not bank_acc):
                st.error("❌ Please select BOTH the Custodial and Bank accounts.")
            elif t_type == "Sinking Fund Expense" and (not sf_acc or not bank_acc):
                st.error("❌ Please select BOTH the Sinking Fund and Bank account.")
            elif t_type == "Income" and not t_acc:
                st.error("❌ Please select a 'Deposit To' account.")
            elif t_type == "Transfer" and (not f_acc or not t_acc):
                st.error("❌ Please select both 'From' and 'To' accounts.")
            elif t_type == "Increase Loan" and not t_acc:
                st.error("❌ Please select a Loan account.")
            elif t_type == "Increase Loan" and not desc.strip():
                st.error("❌ Description is MANDATORY when increasing a loan.")
            else:
                import time
                batch_id = f" [Batch:{int(time.time() * 1000)}]"
                final_cat = category if category else "Others"
                
                # We will track if any part of the execution was routed to the future schedule
                was_scheduled = False
                
                if t_type == "Expense" and is_split:
                    if amt1 > 0: 
                        if process_transaction(tx_date, amt1, f"{desc} (Split 1)", "Expense", account_map[acc1], None, final_cat, remark + batch_id): was_scheduled = True
                    if amt2 > 0: 
                        if process_transaction(tx_date, amt2, f"{desc} (Split 2)", "Expense", account_map[acc2], None, final_cat, remark + batch_id): was_scheduled = True
                
                elif t_type == "Custodial Expense":
                    if amt_bank > 0: 
                        if process_transaction(tx_date, amt_bank, f"{desc} (Actual Payment)", "Expense", account_map[bank_acc], None, final_cat, f"Real payment for {cust_acc}" + batch_id): was_scheduled = True
                    if amt > 0: 
                        if process_transaction(tx_date, amt, f"{desc} (Virtual Deduction)", "Virtual Expense", account_map[cust_acc], None, final_cat, f"Virtual deduction via {bank_acc}" + batch_id): was_scheduled = True
                
                elif t_type == "Sinking Fund Expense":
                    if process_transaction(tx_date, amt, f"{desc} (Actual Payment)", "Expense", account_map[bank_acc], None, final_cat, f"Paid for {sf_acc} goal" + batch_id): was_scheduled = True
                    if process_transaction(tx_date, amt, f"{desc} (Virtual Envelope Deduction)", "Virtual Expense", account_map[sf_acc], None, final_cat, f"Deducted from envelope" + batch_id): was_scheduled = True
                
                elif t_type == "Custodial In":
                    if process_transaction(tx_date, amt, f"{desc} (Custodial)", "Income", None, account_map[bank_acc], final_cat, f"Real deposit for {cust_acc}" + batch_id): was_scheduled = True
                    if process_transaction(tx_date, amt, f"{desc} (Virtual)", "Virtual Funding", None, account_map[cust_acc], final_cat, f"Virtual addition via {bank_acc}" + batch_id): was_scheduled = True
                    
                elif t_type == "Increase Loan":
                    if process_transaction(tx_date, amt, desc, t_type, None, account_map[t_acc], final_cat, remark): was_scheduled = True
                    
                else:
                    f_id = account_map.get(f_acc) if f_acc else None
                    t_id = account_map.get(t_acc) if t_acc else None
                    if process_transaction(tx_date, amt, desc, t_type, f_id, t_id, final_cat, remark): was_scheduled = True
                
                if was_scheduled:
                    msg_container.info(f"📅 Transaction scheduled for future date! ({desc})")
                    st.toast("Scheduled for future!", icon="📅")
                else:
                    msg_container.success(f"✅ Transaction Saved Successfully! ({desc})")
                    st.toast("Transaction Saved!", icon="✅")
                    clear_cache()


# --- MENU: GOALS ---
elif menu == "🎯 Goals":
    st.header("🎯 Sinking Funds Dashboard")
    st.write("Virtually set aside money for future goals without it leaving your bank account.")
    
    goals_raw = df_active[df_active['type'] == 'Sinking Fund'].copy()
    if not goals_raw.empty:
        # Sort Sinking Funds by Closest Due Date
        goals_raw['goal_date_sort'] = pd.to_datetime(goals_raw['goal_date'])
        goals = goals_raw.sort_values(by='goal_date_sort', na_position='last')
        
        total_monthly_commitment = 0.0
        for _, row in goals.iterrows():
            remark = row['remark'] if pd.notna(row['remark']) else ""
            if '[Auto:True]' in remark:
                term_match = re.search(r'\[Term:(\d+)\]', remark)
                term = int(term_match.group(1)) if term_match else 12
                term = max(1, term)
                total_monthly_commitment += (row['goal_amount'] / term)
        
        col_t1, col_t2 = st.columns(2)
        total_saved = goals['balance'].sum()
        col_t1.metric("Total Virtual Funds Saved", f"${total_saved:,.2f}")
        col_t2.metric("Total Auto-Fund Commitment", f"${total_monthly_commitment:,.2f} / month")
        st.divider()

        cols = st.columns(3)
        for i, (index, row) in enumerate(goals.iterrows()):
            col = cols[i % 3] 
            
            with col:
                with st.container(border=True):
                    st.subheader(f"🏷️ {row['name']}")
                    
                    remark = row['remark'] if pd.notna(row['remark']) else ""
                    is_auto = '[Auto:True]' in remark
                    
                    term_match = re.search(r'\[Term:(\d+)\]', remark)
                    term = int(term_match.group(1)) if term_match else 12
                    term = max(1, term)
                    
                    # Clean the remark for display
                    clean_remark = re.sub(r'\[Term:\d+\]', '', remark)
                    clean_remark = clean_remark.replace('[Auto:True]', '').replace('[Auto:False]', '').strip()
                    
                    # MOVED: Show the Remark/Notes on the front of the card
                    if clean_remark:
                        st.info(f"📝 {clean_remark}")
                    
                    monthly_contrib = row['goal_amount'] / term if term > 0 else 0
                    
                    st.metric("Current Saved", f"${row['balance']:,.2f}")
                    goal_str = row['goal_date'].strftime("%d %b %Y") if pd.notnull(row['goal_date']) else "No Date"
                    st.caption(f"**Target:** ${row['goal_amount']:,.2f} by {goal_str}")
                    
                    st.progress(min(row['balance'] / (row['goal_amount'] or 1), 1.0))
                    
                    # --- FIXED MATH LOGIC ---
                    today = date.today()
                    expected_bal = 0.0
                    if pd.notnull(row['goal_date']):
                        months_left = (row['goal_date'].year - today.year) * 12 + (row['goal_date'].month - today.month)
                        months_elapsed = (term - months_left) + 1
                        months_elapsed = max(0, min(term, months_elapsed)) 
                        expected_bal = months_elapsed * monthly_contrib

                    if is_auto:
                        st.success(f"🔄 **Auto-Fund:** ${monthly_contrib:,.2f} / mo")
                    else:
                        st.info(f"⏸️ **Manual:** ${monthly_contrib:,.2f} / mo")
                        
                    if row['balance'] >= row['goal_amount'] and row['goal_amount'] > 0:
                        st.success("🎉 Goal Reached!")
                    elif row['balance'] < expected_bal:
                        st.error(f"📉 Behind by ${expected_bal - row['balance']:,.2f}")
                    else:
                        st.success(f"🟢 On Track! (Expected: ${expected_bal:,.2f})")
                        
                    with st.expander("⚙️ Edit Settings"):
                        if not is_auto and monthly_contrib > 0 and row['balance'] < row['goal_amount']:
                            st.write("**Manual Funding**")
                            default_fund = min(monthly_contrib, row['goal_amount'] - row['balance'])
                            amt_to_add = st.number_input("Amount to Fund Now ($)", min_value=0.01, max_value=float(row['goal_amount'] - row['balance']), value=float(default_fund), key=f"amt_{row['id']}")
                            
                            if st.button("➕ Submit Funding", key=f"fund_{row['id']}", use_container_width=True):
                                add_transaction(today, amt_to_add, f"Manual Envelope Funding: {row['name']}", "Virtual Funding", None, row['id'], "Fund", "Manual envelope funding")
                                clear_cache()
                                st.rerun()
                            st.divider()
                                
                        st.write("**Goal Configuration**")
                        new_goal = st.number_input("Goal Target ($)", min_value=0.0, value=float(row['goal_amount']), key=f"goal_{row['id']}")
                        new_term = st.number_input("Term (Total Months)", min_value=1, value=term, key=f"term_{row['id']}")
                        
                        current_date = row['goal_date'] if pd.notnull(row['goal_date']) else date.today()
                        new_date = st.date_input("Target Date", value=current_date, key=f"date_{row['id']}")
                        
                        new_notes = st.text_input("Notes", value=clean_remark, key=f"note_{row['id']}")
                        new_auto = st.checkbox("☑️ Enable Auto-Funding (1st of Month)", value=is_auto, key=f"auto_{row['id']}")
                        
                        if st.button("💾 Save Settings", key=f"save_{row['id']}", use_container_width=True):
                            new_tags = f"[Term:{new_term}] [Auto:{new_auto}]"
                            final_remark = f"{new_tags} {new_notes}".strip()
                            
                            supabase.table('accounts').update({
                                'goal_amount': new_goal,
                                'goal_date': str(new_date),
                                'remark': final_remark
                            }).eq('id', row['id']).execute()
                            
                            clear_cache()
                            st.rerun()
    else:
        st.info("No Sinking Funds created yet. Go to Settings to add one!")


# --- MENU: SCHEDULE ---
elif menu == "📅 Schedule":
    st.header("📅 Manage Future Payments")
    
    with st.expander("➕ Add Schedule", expanded=False):
        with st.form("sch_form"):
            s_desc = st.text_input("Description")
            c1, c2, c3 = st.columns(3)
            s_amount = c1.number_input("Amount", min_value=0.01)
            s_freq = c2.selectbox("Frequency", ["Monthly", "One-Time"])
            s_date = c3.date_input("Start Date", datetime.today())
            
            s_cat_opts = get_categories()['name'].tolist()
            s_cat = st.selectbox("Category", s_cat_opts, index=None, placeholder="Select Category (Optional)...")
            s_manual = st.checkbox("🔔 Manual Reminder?", value=False)
            s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
            
            s_from, s_to = None, None
            if s_type == "Expense": s_from = st.selectbox("From Account", non_loan_accounts, index=None, placeholder="Select Account...")
            elif s_type == "Income": s_to = st.selectbox("To Account", account_list, index=None, placeholder="Select Account...")
            elif s_type == "Transfer":
                s_from = st.selectbox("From", non_loan_accounts, key="s_f", index=None, placeholder="Select Source...")
                s_to = st.selectbox("To", account_list, key="s_t", index=None, placeholder="Select Destination...")
                
            if st.form_submit_button("Schedule It"):
                if s_type == "Expense" and not s_from:
                    st.error("❌ Please select a 'From Account'.")
                elif s_type == "Income" and not s_to:
                    st.error("❌ Please select a 'To Account'.")
                elif s_type == "Transfer" and (not s_from or not s_to):
                    st.error("❌ Please select both Source and Destination accounts.")
                else:
                    final_sch_cat = s_cat if s_cat else "Others"
                    f_id = account_map.get(s_from) if s_from else None
                    t_id = account_map.get(s_to) if s_to else None
                    
                    supabase.table('schedule').insert({
                        "description": s_desc, "amount": s_amount, "type": s_type, 
                        "from_account_id": f_id, "to_account_id": t_id, 
                        "frequency": s_freq, "next_run_date": str(s_date),
                        "is_manual": s_manual, "category": final_sch_cat
                    }).execute()
                    st.success("Scheduled!")
                    clear_cache()

    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.write("### 🗓️ Upcoming Items")
        df_upcoming = pd.DataFrame(upcoming)
        
        # UPGRADE: Interactive Selectable Dataframe
        event_sch = st.dataframe(
            df_upcoming[['next_run_date', 'description', 'amount', 'frequency']], 
            hide_index=True, 
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row"
        )
        
        st.write("### 🗑️ Cancel Scheduled Item")
        
        # UPGRADE: Click-to-Select Safety Preview Logic
        if len(event_sch.selection.rows) > 0:
            sel_idx = event_sch.selection.rows[0]
            sel_row = df_upcoming.iloc[sel_idx]
            
            with st.container(border=True):
                st.warning("⚠️ **Cancellation Preview & Impact Analysis**")
                st.write(f"**Target:** {sel_row['description']} (${sel_row['amount']:,.2f})")
                st.write(f"**Scheduled For:** {sel_row['next_run_date']}")
                
                # Retrieve Account Names for clearer impact analysis
                id_to_name = {v: k for k, v in account_map.items()}
                f_acc = id_to_name.get(sel_row['from_account_id'], "None")
                t_acc = id_to_name.get(sel_row['to_account_id'], "None")
                st.write(f"**Accounts Involved:** From [{f_acc}] ➔ To [{t_acc}]")
                
                st.write("**Impact:** No current balances will be affected. This future payment will simply be removed from your timeline.")
                
                if st.button("🚨 Confirm Cancel Scheduled Payment", use_container_width=True):
                    supabase.table('schedule').delete().eq("id", int(sel_row['id'])).execute()
                    st.success("Scheduled payment cancelled!")
                    clear_cache()
                    st.rerun()
        else:
            st.info("👆 **Click on any row** in the table above to preview and cancel a scheduled payment.")



# --- MENU: SETTINGS ---
elif menu == "⚙️ Settings":
    st.header("🔧 Configuration")
    
    st.write("### 🏷️ Edit Categories")
    df_cats = get_categories()
    
    if not df_cats.empty:
        st.data_editor(
            df_cats[['name', 'type', 'budget_limit']], 
            key="cat_editor_v3", 
            num_rows="dynamic",
            hide_index=True, 
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=["Expense", "Income", "Fund"]), 
                "budget_limit": st.column_config.NumberColumn("Budget Limit", format="$%.2f", step=0.01) 
            }
        )
        if st.button("💾 Save Categories"):
            apply_editor_changes('categories', df_cats, 'cat_editor_v3')
            st.success("Categories Updated!")
            st.rerun()

    st.divider()

    st.write("### 🏦 Edit Accounts")
    df_all_accounts = get_accounts(show_inactive=True)
    
    if not df_all_accounts.empty:
        cols_to_edit = [
            'name', 'type', 'balance', 'currency', 'manual_exchange_rate',
            'goal_amount', 'goal_date', 'include_net_worth', 'is_liquid_asset',
            'sort_order', 'is_active', 'remark'
        ]
        
        st.data_editor(
            df_all_accounts[cols_to_edit], 
            key="account_editor_v3", 
            num_rows="dynamic",
            hide_index=True, 
            column_config={
                "type": st.column_config.SelectboxColumn(
                    "Type", 
                    options=["Bank", "Credit Card", "Custodial", "Sinking Fund", "Loan", "Investment"]
                ),
                "is_active": st.column_config.CheckboxColumn("Active?", default=True),
                "goal_date": st.column_config.DateColumn("Goal Date"),
                "goal_amount": st.column_config.NumberColumn("Goal Amount", format="%.2f", step=0.01), 
                "manual_exchange_rate": st.column_config.NumberColumn("Rate", format="%.4f"),
            }
        )
        if st.button("💾 Save Accounts"):
            apply_editor_changes('accounts', df_all_accounts, 'account_editor_v3')
            st.success("Accounts Updated!")
            st.rerun()


# --- MENU: REPORTS ---
elif menu == "📈 Reports":
    st.header("📈 Reports & Analytics")
    st.write("View detailed spending or income by category over time.")
    
    with st.form("report_form"):
        col1, col2, col3 = st.columns(3)
        start_date = col1.date_input("Start Date", date.today().replace(day=1))
        end_date = col2.date_input("End Date", date.today())
        
        cat_df = get_categories()
        all_cats = ["All Categories"] + cat_df['name'].tolist() if not cat_df.empty else ["All Categories"]
        selected_cat = col3.selectbox("Category", all_cats)
        
        generate = st.form_submit_button("Generate Report")
        
    if generate:
        query = supabase.table('transactions').select('*').gte('date', start_date).lte('date', end_date)
        if selected_cat != "All Categories":
            query = query.eq('category', selected_cat)
        
        report_txs = query.order('date', desc=True).order('id', desc=True).execute().data
        
        if report_txs:
            df_report = pd.DataFrame(report_txs)
            
            total_expense = df_report[df_report['type'].isin(['Expense', 'Virtual Expense'])]['amount'].sum()
            total_income = df_report[df_report['type'].isin(['Income', 'Virtual Funding'])]['amount'].sum()
            
            st.subheader(f"Summary: {selected_cat}")
            c_rep1, c_rep2 = st.columns(2)
            c_rep1.metric("Total Expense in Period", f"${total_expense:,.2f}")
            c_rep2.metric("Total Income in Period", f"${total_income:,.2f}")
            
            st.divider()
            
            id_to_name = {v: k for k, v in account_map.items()}
            view_data = []
            
            for _, row in df_report.iterrows():
                f_name = id_to_name.get(row['from_account_id'], "")
                t_name = id_to_name.get(row['to_account_id'], "")
                
                acc_involved = ""
                if f_name and t_name: acc_involved = f"{f_name} ➔ {t_name}"
                elif f_name: acc_involved = f"From: {f_name}"
                elif t_name: acc_involved = f"To: {t_name}"
                
                amt = row['amount']
                if row['type'] in ['Expense', 'Virtual Expense', 'Increase Loan']:
                    amt = -amt 
                    
                raw_remark = row.get('remark') or ""
                clean_remark = raw_remark.split(' [Batch:')[0].strip()
                
                view_data.append({
                    "Date": row['date'],
                    "Description": row['description'],
                    "Category": row['category'],
                    "Amount": amt,
                    "Accounts": acc_involved,
                    "Type": row['type'],
                    "Notes": clean_remark
                })
                
            st.dataframe(pd.DataFrame(view_data), hide_index=True, use_container_width=True)
        else:
            st.info("No transactions found for this date range and category.")
