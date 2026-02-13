import streamlit as st
import hmac
from supabase import create_client, Client
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta

# --- 1. SECURITY & SETUP ---
st.set_page_config(page_title="My Finance", page_icon="💰", layout="wide")

def check_password():
    def password_entered():
        if hmac.compare_digest(st.session_state["password"], st.secrets["APP_PASSWORD"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

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
    
    return df.sort_values(by=['type', 'sort_order', 'name'])

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
    return df.sort_values('name')

def update_balance(account_id, amount_change):
    if not account_id: return 
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

# --- BULLETPROOF STATE-TRACKING SAVE FUNCTION ---
def apply_editor_changes(table_name, original_df, editor_key):
    changes = st.session_state[editor_key]
    
    # 1. PROCESS DELETIONS
    for idx in changes.get("deleted_rows", []):
        row_id = original_df.iloc[idx]['id']
        supabase.table(table_name).delete().eq('id', row_id).execute()
        
    # 2. PROCESS EDITS TO EXISTING ROWS
    for idx_str, edits in changes.get("edited_rows", {}).items():
        idx = int(idx_str)
        row_id = original_df.iloc[idx]['id']
        
        # FIX: Remove Streamlit's hidden index if it snuck in
        edits.pop('_index', None)
        
        # Safely convert dates to text for database saving
        if 'goal_date' in edits:
            edits['goal_date'] = str(edits['goal_date']) if edits['goal_date'] else None
            
        if edits:
            supabase.table(table_name).update(edits).eq('id', row_id).execute()
        
    # 3. PROCESS NEW ADDITIONS
    for new_row in changes.get("added_rows", []):
        
        # FIX: Strip out Streamlit's hidden variables to prevent Supabase crashes
        new_row.pop('_index', None) 
        new_row.pop('id', None) 
        
        if not new_row.get('name') or str(new_row.get('name')).strip() == "":
            continue # Skip accidentally created blank rows entirely
            
        # Provide safe default values
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

    if type == "Expense": update_balance(from_acc_id, -amount)
    elif type == "Income": update_balance(to_acc_id, amount)
    elif type == "Increase Loan": update_balance(to_acc_id, -amount) 
    elif type == "Transfer":
        if from_acc_id: update_balance(from_acc_id, -amount)
        if to_acc_id: update_balance(to_acc_id, amount)
    
    clear_cache()

def delete_transaction(tx_id):
    tx_data = supabase.table('transactions').select("*").eq('id', tx_id).execute().data
    if not tx_data:
        return False
        
    tx = tx_data[0]
    amt = float(tx['amount'])
    t_type = tx['type']
    
    if t_type == "Expense": 
        update_balance(tx['from_account_id'], amt) 
    elif t_type == "Income": 
        update_balance(tx['to_account_id'], -amt) 
    elif t_type == "Increase Loan": 
        update_balance(tx['to_account_id'], amt) 
    elif t_type == "Transfer":
        if tx['from_account_id']: update_balance(tx['from_account_id'], amt)
        if tx['to_account_id']: update_balance(tx['to_account_id'], -amt)

    supabase.table('transactions').delete().eq('id', tx_id).execute()
    clear_cache()
    return True

# --- 3. APP START ---
st.title("💰 My Wealth Manager")
df_active = get_accounts(show_inactive=False)
account_map = dict(zip(df_active['name'], df_active['id']))
account_list = df_active['name'].tolist() if not df_active.empty else []

non_loan_accounts = df_active[df_active['type'] != 'Loan']['name'].tolist() if not df_active.empty else []

# --- SIDEBAR NAVIGATION ---
st.sidebar.title("Navigation")
menu = st.sidebar.radio("Go to:", ["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- MENU: OVERVIEW ---
if menu == "📊 Overview":
    st.header("📊 Overview")
    if not df_active.empty:
        df_calc = df_active.copy()
        df_calc['sgd_value'] = df_calc['balance'] * df_calc['manual_exchange_rate']
        net_worth = df_calc[df_calc['include_net_worth'] == True]['sgd_value'].sum()
        liquid = df_calc[df_calc['is_liquid_asset'] == True]['sgd_value'].sum()
    else:
        net_worth, liquid = 0, 0
    
    c1, c2 = st.columns(2)
    c1.metric("Net Worth (SGD)", f"${net_worth:,.2f}") 
    c2.metric("Liquid Assets (SGD)", f"${liquid:,.2f}")
    
    st.divider()

    st.subheader("💳 Current Balances")
    if not df_active.empty:
        df_display = df_active[['name', 'type', 'balance', 'currency']].copy()
        st.dataframe(
            df_display, 
            hide_index=True, 
            use_container_width=True,
            column_config={
                "name": "Account Name",
                "type": "Type",
                "balance": st.column_config.NumberColumn("Balance", format="%.2f"),
                "currency": "Currency"
            }
        )

    st.divider()

    st.subheader("📜 Account Statement")
    selected_acc_name = st.selectbox("Select Account to View Statement:", account_list, key="ledger_select")
    
    if selected_acc_name:
        sel_id = account_map[selected_acc_name]
        txs = supabase.table('transactions').select("*") \
            .or_(f"from_account_id.eq.{sel_id},to_account_id.eq.{sel_id}") \
            .order("date", desc=True).limit(50).execute().data
            
        if txs:
            df_tx = pd.DataFrame(txs)
            view_data = []
            for _, row in df_tx.iterrows():
                is_inflow = row['to_account_id'] == sel_id
                desc = row['description']
                amt = row['amount']
                if row['type'] == 'Transfer':
                    amt = amt if is_inflow else -amt
                    desc = f"Transfer: {desc}"
                elif row['type'] == 'Expense':
                    amt = -amt
                elif row['type'] == 'Increase Loan':
                    amt = -amt 
                
                view_data.append({
                    "ID": row['id'], 
                    "Date": row['date'], "Description": desc, "Amount": amt, 
                    "Category": row['category'], "Type": row['type']
                })
            st.dataframe(pd.DataFrame(view_data), use_container_width=True, hide_index=True)
            
            st.divider()
            
            st.write("### 🗑️ Delete / Reverse Transaction")
            st.info("To edit a mistake, delete its ID here to restore your balances, then re-enter it correctly.")
            
            del_col1, del_col2 = st.columns([1, 2])
            with del_col1:
                del_id = st.number_input("Enter Transaction ID to Delete", step=1, min_value=0)
            with del_col2:
                st.write("") 
                st.write("")
                if st.button("⚠️ Delete & Restore Balances"):
                    if del_id > 0:
                        success = delete_transaction(del_id)
                        if success:
                            st.success(f"Transaction {del_id} deleted and balances restored!")
                            st.rerun()
                        else:
                            st.error("Transaction ID not found.")
                    else:
                        st.warning("Please enter a valid ID.")
        else:
            st.info("No recent transactions found.")

# --- MENU: ENTRY ---
elif menu == "📝 Entry":
    st.header("📝 New Transaction")
    
    msg_container = st.empty() 
    
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial Expense", "Custodial In", "Increase Loan"], horizontal=True)
    
    is_split = False
    if t_type == "Expense":
        is_split = st.checkbox("🔀 Split Payment (Pay from 2 sources)")
    
    with st.form("entry_form"):
        c1, c2 = st.columns(2)
        tx_date = c1.date_input("Date", datetime.today())
        
        if t_type == "Expense" and is_split:
            st.info("Split Payment: Amount 1 + Amount 2 = Total Expense")
            col_a, col_b = st.columns(2)
            with col_a:
                acc1 = st.selectbox("Source 1", non_loan_accounts, key="src1")
                amt1 = st.number_input("Amount 1", min_value=0.0, format="%.2f")
            with col_b:
                acc2 = st.selectbox("Source 2", non_loan_accounts, key="src2")
                amt2 = st.number_input("Amount 2", min_value=0.0, format="%.2f")
        
        elif t_type == "Expense":
            f_acc = st.selectbox("Paid From", non_loan_accounts)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type == "Custodial Expense":
            st.warning("🔻 Deducts from Virtual Custodial Account AND Actual Bank Account")
            c_a, c_b = st.columns(2)
            cust_opts = df_active[df_active['type']=='Custodial']['name']
            bank_opts = df_active[df_active['type']=='Bank']['name']
            cust_acc = c_a.selectbox("Custodial Account (Virtual)", cust_opts)
            bank_acc = c_b.selectbox("Paid via Bank (Actual)", bank_opts)
            amt = c2.number_input("Total Amount", min_value=0.01)

        elif t_type == "Custodial In":
            st.info("🔼 Deposits Virtual money to Custodial AND Actual money to Bank")
            c_a, c_b = st.columns(2)
            cust_opts = df_active[df_active['type']=='Custodial']['name']
            bank_opts = df_active[df_active['type']=='Bank']['name']
            cust_acc = c_a.selectbox("Custodial Account (Virtual)", cust_opts)
            bank_acc = c_b.selectbox("Deposit to Bank (Actual)", bank_opts)
            amt = c2.number_input("Total Amount", min_value=0.01)

        elif t_type == "Income":
            t_acc = st.selectbox("Deposit To", account_list)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type == "Transfer":
            c_a, c_b = st.columns(2)
            f_acc = c_a.selectbox("From", non_loan_accounts)
            t_acc = c_b.selectbox("To", account_list)
            amt = c2.number_input("Amount", min_value=0.01)
            
        elif t_type == "Increase Loan":
            st.info("📈 Increase the amount you owe on a loan (e.g. adding interest or borrowing more).")
            loan_opts = df_active[df_active['type'] == 'Loan']['name'].tolist()
            if len(loan_opts) == 0:
                st.warning("No Loan accounts found. Add one in Settings first.")
                t_acc = None
            else:
                t_acc = st.selectbox("Select Loan Account", loan_opts)
            amt = c2.number_input("Amount to Add to Loan", min_value=0.01)

        df_cats_full = get_categories()
        if df_cats_full.empty:
            cat_options = []
        else:
            if t_type in ["Income", "Custodial In"]:
                cat_options = df_cats_full[df_cats_full['type'] == 'Income']['name'].tolist()
            elif t_type == "Transfer":
                cat_options = df_cats_full['name'].tolist() 
            else:
                cat_options = df_cats_full[df_cats_full['type'].isin(['Expense', 'Fund'])]['name'].tolist()
                
        category = st.selectbox("Category", [""] + cat_options)
        
        desc = st.text_input("Description")
        remark = st.text_area("Notes", height=2)
        
        submitted = st.form_submit_button("Submit Transaction")
        
        if submitted:
            final_cat = category if category.strip() != "" else "Others"
            
            if t_type == "Increase Loan" and desc.strip() == "":
                st.error("❌ Details (Description) are MANDATORY when increasing a loan amount.")
            elif t_type == "Increase Loan" and not t_acc:
                st.error("❌ No loan account selected.")
            else:
                if t_type == "Expense" and is_split:
                    if amt1 > 0:
                        add_transaction(tx_date, amt1, f"{desc} (Split 1)", "Expense", account_map[acc1], None, final_cat, remark)
                    if amt2 > 0:
                        add_transaction(tx_date, amt2, f"{desc} (Split 2)", "Expense", account_map[acc2], None, final_cat, remark)
                
                elif t_type == "Custodial Expense":
                    add_transaction(tx_date, amt, f"{desc} (Custodial)", "Expense", account_map[bank_acc], None, final_cat, f"Real payment for {cust_acc}")
                    add_transaction(tx_date, amt, f"{desc} (Virtual)", "Expense", account_map[cust_acc], None, final_cat, f"Virtual deduction via {bank_acc}")
                
                elif t_type == "Custodial In":
                    add_transaction(tx_date, amt, f"{desc} (Custodial)", "Income", None, account_map[bank_acc], final_cat, f"Real deposit for {cust_acc}")
                    add_transaction(tx_date, amt, f"{desc} (Virtual)", "Income", None, account_map[cust_acc], final_cat, f"Virtual addition via {bank_acc}")
                    
                elif t_type == "Increase Loan":
                    add_transaction(tx_date, amt, desc, t_type, None, account_map[t_acc], final_cat, remark)
                    
                else:
                    f_id = account_map.get(f_acc) if 'f_acc' in locals() and f_acc else None
                    t_id = account_map.get(t_acc) if 't_acc' in locals() and t_acc else None
                    add_transaction(tx_date, amt, desc, t_type, f_id, t_id, final_cat, remark)
                
                msg_container.success(f"✅ Transaction Saved Successfully! ({desc})")
                st.toast("Transaction Saved!", icon="✅")
                clear_cache()

# --- MENU: GOALS ---
elif menu == "🎯 Goals":
    st.header("🎯 Sinking Funds Dashboard")
    goals = df_active[df_active['type'] == 'Sinking Fund']
    if not goals.empty:
        for i, (index, row) in enumerate(goals.iterrows()):
            st.write(f"**{row['name']}** - ${row['balance']:,.2f} / ${row['goal_amount']:,.2f}")
            st.progress(min(row['balance'] / (row['goal_amount'] or 1), 1.0))
    else:
        st.info("No Sinking Funds created yet.")

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
            s_cat = st.selectbox("Category", [""] + s_cat_opts)
            s_manual = st.checkbox("🔔 Manual Reminder?", value=False)
            s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
            
            s_from, s_to = None, None
            if s_type == "Expense": s_from = st.selectbox("From Account", non_loan_accounts)
            elif s_type == "Income": s_to = st.selectbox("To Account", account_list)
            elif s_type == "Transfer":
                s_from = st.selectbox("From", non_loan_accounts, key="s_f")
                s_to = st.selectbox("To", account_list, key="s_t")
                
            if st.form_submit_button("Schedule It"):
                final_sch_cat = s_cat if s_cat.strip() != "" else "Others"
                f_id = account_map.get(s_from)
                t_id = account_map.get(s_to)
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
        st.dataframe(pd.DataFrame(upcoming)[['next_run_date', 'description', 'amount', 'frequency']], hide_index=True, use_container_width=True)
        
        with st.popover("🗑️ Delete Item"):
            del_id = st.number_input("ID to delete", step=1)
            if st.button("Delete Schedule"):
                supabase.table('schedule').delete().eq("id", del_id).execute()
                st.rerun()

# --- MENU: SETTINGS ---
elif menu == "⚙️ Settings":
    st.header("🔧 Configuration")
    
    st.write("### 🏷️ Edit Categories")
    df_cats = get_categories()
    
    if not df_cats.empty:
        # FIX: Added `_v2` to force Streamlit to wipe its cache and actually drop the ID column visually
        st.data_editor(
            df_cats[['name', 'type', 'budget_limit']], 
            key="cat_editor_v2", 
            num_rows="dynamic",
            hide_index=True, 
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=["Expense", "Income", "Fund"]), 
                "budget_limit": st.column_config.NumberColumn("Budget Limit", format="$%.2f", step=0.01) 
            }
        )
        if st.button("💾 Save Categories"):
            apply_editor_changes('categories', df_cats, 'cat_editor_v2')
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
        
        # FIX: Added `_v2` to force Streamlit to wipe its cache and actually drop the ID column visually
        st.data_editor(
            df_all_accounts[cols_to_edit], 
            key="account_editor_v2",
            num_rows="dynamic",
            hide_index=True, 
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=["Bank", "Credit Card", "Custodial", "Sinking Fund", "Loan", "Investment"]),
                "is_active": st.column_config.CheckboxColumn("Active?", default=True),
                "goal_date": st.column_config.DateColumn("Goal Date"),
                "goal_amount": st.column_config.NumberColumn("Goal Amount", format="%.2f", step=0.01), 
                "manual_exchange_rate": st.column_config.NumberColumn("Rate", format="%.4f"),
            }
        )
        if st.button("💾 Save Accounts"):
            apply_editor_changes('accounts', df_all_accounts, 'account_editor_v2')
            st.success("Accounts Updated!")
            st.rerun()
