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
        'goal_amount': 0
    }
    for col, val in defaults.items():
        if col not in df.columns: df[col] = val
    
    if not show_inactive:
        df = df[df['is_active'] == True]
        
    return df.sort_values(by=['sort_order', 'name'])

@st.cache_data(ttl=3600)
def get_categories(type_filter=None):
    query = supabase.table('categories').select("*")
    if type_filter:
        query = query.eq('type', type_filter)
    data = query.execute().data
    if not data: return pd.DataFrame(columns=['id', 'name', 'type', 'budget_limit'])
    
    df = pd.DataFrame(data)
    if 'budget_limit' not in df.columns: df['budget_limit'] = 0.0
    return df.sort_values('name')

def update_balance(account_id, amount_change):
    if not account_id: return 
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def save_bulk_editor(table_name, df_edited):
    records = df_edited.to_dict('records')
    for row in records:
        if table_name == 'accounts':
            if pd.isna(row.get('goal_amount')): row['goal_amount'] = 0
            if pd.isna(row.get('manual_exchange_rate')): row['manual_exchange_rate'] = 1.0
            data = {
                "name": row['name'], "balance": row['balance'], "type": row['type'],
                "currency": row['currency'], "manual_exchange_rate": row['manual_exchange_rate'],
                "remark": row['remark'], "is_active": row['is_active'], "sort_order": row['sort_order'],
                "include_net_worth": row['include_net_worth'], "is_liquid_asset": row['is_liquid_asset']
            }
        elif table_name == 'categories':
             if pd.isna(row.get('budget_limit')): row['budget_limit'] = 0
             data = {
                 "name": row['name'], "type": row['type'], "budget_limit": row['budget_limit']
             }
        
        row_id = row.get('id')
        if row_id and pd.notna(row_id):
            supabase.table(table_name).update(data).eq("id", row_id).execute()
        else:
            supabase.table(table_name).insert(data).execute()
    clear_cache()

def add_transaction(date, amount, description, type, from_acc_id, to_acc_id, category, remark):
    supabase.table('transactions').insert({
        "date": str(date), "amount": amount, "description": description, "type": type,
        "from_account_id": from_acc_id, "to_account_id": to_acc_id, "category": category,
        "remark": remark
    }).execute()

    if type == "Expense": update_balance(from_acc_id, -amount)
    elif type in ["Income", "Refund"]: update_balance(to_acc_id, amount)
    elif type == "Transfer":
        if from_acc_id: update_balance(from_acc_id, -amount)
        if to_acc_id: update_balance(to_acc_id, amount)
    
    clear_cache()

# --- 3. APP START ---
st.title("💰 My Wealth Manager")
df_active = get_accounts(show_inactive=False)
account_map = dict(zip(df_active['name'], df_active['id']))
account_list = df_active['name'].tolist() if not df_active.empty else []
non_loan_accounts = df_active[df_active['type'] != 'Loan']['name'].tolist() if not df_active.empty else []

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW ---
with tab1:
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

    st.subheader("📜 Account Statement")
    selected_acc_name = st.selectbox("Select Account to View:", account_list, key="ledger_select")
    
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
                view_data.append({
                    "Date": row['date'], "Description": desc, "Amount": amt, 
                    "Category": row['category'], "Type": row['type']
                })
            st.dataframe(pd.DataFrame(view_data), use_container_width=True)
        else:
            st.info("No recent transactions found.")

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial Expense", "Custodial In"], horizontal=True)
    
    is_split = False
    if t_type == "Expense":
        is_split = st.checkbox("🔀 Split Payment (Pay from 2 sources)")
    
    with st.form("entry_form"):
        c1, c2 = st.columns(2)
        tx_date = c1.date_input("Date", datetime.today())
        
        # Branching Logic
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

        elif t_type in ["Income", "Refund"]:
            t_acc = st.selectbox("Deposit To", account_list)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type in ["Transfer", "Custodial In"]:
            c_a, c_b = st.columns(2)
            f_acc = c_a.selectbox("From", non_loan_accounts)
            t_acc = c_b.selectbox("To", account_list)
            amt = c2.number_input("Amount", min_value=0.01)

        # --- CATEGORY FIX: Allow Blank ---
        cat_type = "Income" if t_type == "Income" else "Expense"
        cat_options = get_categories(cat_type)['name'].tolist()
        # Add an empty string at the start to allow "Blank" selection
        category = st.selectbox("Category", [""] + cat_options)
        
        desc = st.text_input("Description")
        remark = st.text_area("Notes", height=2)
        
        submitted = st.form_submit_button("Submit Transaction")
        
        if submitted:
            # DEFAULT TO "Others" IF BLANK
            final_cat = category if category.strip() != "" else "Others"
            
            if t_type == "Expense" and is_split:
                if amt1 > 0:
                    add_transaction(tx_date, amt1, f"{desc} (Split 1)", "Expense", account_map[acc1], None, final_cat, remark)
                if amt2 > 0:
                    add_transaction(tx_date, amt2, f"{desc} (Split 2)", "Expense", account_map[acc2], None, final_cat, remark)
            
            elif t_type == "Custodial Expense":
                add_transaction(tx_date, amt, f"{desc} (Custodial)", "Expense", account_map[bank_acc], None, final_cat, f"Real payment for {cust_acc}")
                add_transaction(tx_date, amt, f"{desc} (Virtual)", "Expense", account_map[cust_acc], None, final_cat, f"Virtual deduction via {bank_acc}")
                
            else:
                f_id = account_map.get(f_acc) if 'f_acc' in locals() and f_acc else None
                t_id = account_map.get(t_acc) if 't_acc' in locals() and t_acc else None
                add_transaction(tx_date, amt, desc, t_type, f_id, t_id, final_cat, remark)
            
            st.success("Transaction Saved!")
            clear_cache()

# --- TAB 3: GOALS ---
with tab3:
    st.subheader("🎯 Sinking Funds Dashboard")
    goals = df_active[df_active['type'] == 'Sinking Fund']
    if not goals.empty:
        for i, (index, row) in enumerate(goals.iterrows()):
            st.write(f"**{row['name']}** - ${row['balance']:,.2f} / ${row['goal_amount']:,.2f}")
            st.progress(min(row['balance'] / (row['goal_amount'] or 1), 1.0))

# --- TAB 4: SCHEDULE ---
with tab4:
    st.subheader("Manage Future Payments")
    
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

# --- TAB 5: SETTINGS ---
with tab5:
    st.subheader("🔧 Configuration")
    
    st.write("### 🏷️ Edit Categories")
    st.caption("Click the '+' row at bottom to add. **Leave ID blank** for new rows.")
    
    df_cats = get_categories()
    if not df_cats.empty:
        edited_cats = st.data_editor(
            df_cats[['id', 'name', 'type', 'budget_limit']], 
            key="cat_editor",
            num_rows="dynamic",
            disabled=['id'],
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=["Expense", "Income"]),
                "budget_limit": st.column_config.NumberColumn("Budget Limit", format="$%.2f")
            }
        )
        if st.button("💾 Save Categories"):
            save_bulk_editor('categories', edited_cats)
            st.success("Categories Updated!")

    st.divider()

    st.write("### 🏦 Edit Accounts")
    df_all_accounts = get_accounts(show_inactive=True)
    if not df_all_accounts.empty:
        edited_accs = st.data_editor(
            df_all_accounts[['id', 'name', 'type', 'balance', 'currency', 'sort_order', 'is_active']], 
            key="account_editor",
            num_rows="dynamic",
            disabled=['id'],
            column_config={
                "type": st.column_config.SelectboxColumn("Type", options=["Bank", "Credit Card", "Custodial", "Sinking Fund", "Loan", "Investment"]),
                "is_active": st.column_config.CheckboxColumn("Active?", default=True),
            }
        )
        if st.button("💾 Save Accounts"):
            save_bulk_editor('accounts', edited_accs)
            st.success("Accounts Updated!")
