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

# --- 2. CACHED HELPER FUNCTIONS ---

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
        'include_net_worth': True, 'is_liquid_asset': True
    }
    for col, val in defaults.items():
        if col not in df.columns: df[col] = val
    
    if not show_inactive:
        df = df[df['is_active'] == True]
        
    return df.sort_values(by=['sort_order', 'name'])

@st.cache_data(ttl=3600)
def get_categories(type_filter=None):
    # Fetch full objects so we can get budget_limit if needed
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
    # Fetch fresh to avoid race conditions
    current = supabase.table('accounts').select("balance").eq("id", account_id).execute().data[0]['balance']
    new_balance = float(current) + float(amount_change)
    supabase.table('accounts').update({"balance": new_balance}).eq("id", account_id).execute()

def update_table_direct(table_name, changes_df):
    """Generic updater for accounts or categories"""
    records = changes_df.to_dict('records')
    for row in records:
        # Sanitize
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
        
        # If ID exists, update. If new (added via data_editor), insert.
        if row.get('id'):
            supabase.table(table_name).update(data).eq("id", row['id']).execute()
        else:
            # Insert new row
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
    elif type in ["Transfer", "Custodial In", "Custodial Out"]:
        # Logic: Transfer moves money FROM -> TO
        if from_acc_id: update_balance(from_acc_id, -amount)
        if to_acc_id: update_balance(to_acc_id, amount)

    clear_cache()

def run_scheduled_transactions():
    today = datetime.today().date()
    tasks = supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', False).execute().data
    count = 0
    if tasks:
        for task in tasks:
            cat = task.get('category', 'Recurring')
            add_transaction(task['next_run_date'], task['amount'], f"🔄 {task['description']}", 
                          task['type'], task['from_account_id'], task['to_account_id'], cat, "Auto-Scheduled")
            
            if task['frequency'] == 'Monthly':
                next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
            else:
                supabase.table('schedule').delete().eq("id", task['id']).execute()
            count += 1
    return count

def get_due_manual_tasks():
    today = datetime.today().date()
    return supabase.table('schedule').select("*").lte('next_run_date', str(today)).eq('is_manual', True).execute().data

# --- 3. ANALYTICS HELPERS ---
def calculate_net_worth_trend(df_accounts):
    """Reconstructs net worth history by reversing transactions"""
    if df_accounts.empty: return pd.DataFrame()
    
    # 1. Get current total Net Worth
    df_nw = df_accounts[df_accounts['include_net_worth'] == True].copy()
    df_nw['sgd_bal'] = df_nw['balance'] * df_nw['manual_exchange_rate']
    current_nw = df_nw['sgd_bal'].sum()
    
    # 2. Get last 90 days transactions
    lookback_days = 90
    start_date = date.today() - timedelta(days=lookback_days)
    
    txs = supabase.table('transactions').select("*").gte('date', str(start_date)).order('date', desc=True).execute().data
    if not txs: return pd.DataFrame()
    
    df_tx = pd.DataFrame(txs)
    df_tx['amount'] = pd.to_numeric(df_tx['amount'])
    df_tx['date'] = pd.to_datetime(df_tx['date']).dt.date
    
    # 3. Create daily timeline
    dates = [date.today() - timedelta(days=i) for i in range(lookback_days + 1)]
    dates.reverse() # Oldest to Newest
    
    history = []
    running_nw = current_nw
    
    # We walk BACKWARDS from today. 
    # Today's NW is known. Yesterday's NW = Today's NW - Income + Expense
    
    # Group transactions by date
    tx_groups = df_tx.groupby('date')
    
    # Re-reverse dates to go from Today -> Past
    dates_desc = sorted(dates, reverse=True)
    
    history_data = []
    
    for d in dates_desc:
        history_data.append({'date': d, 'net_worth': running_nw})
        
        if d in tx_groups.groups:
            day_txs = tx_groups.get_group(d)
            for _, row in day_txs.iterrows():
                amt = row['amount']
                # Reverse logic: If we spent money today, we had MORE yesterday.
                if row['type'] == 'Expense':
                    running_nw += amt # Add back expense
                elif row['type'] in ['Income', 'Refund']:
                    running_nw -= amt # Subtract income
                # Transfers don't change Net Worth usually, unless across tracked/untracked
                # Assuming all transfers are internal for now => No Change
                
    return pd.DataFrame(history_data).sort_values('date')

# --- 4. APP INTERFACE ---
st.title("💰 My Wealth Manager")

if 'scheduler_run' not in st.session_state:
    processed = run_scheduled_transactions()
    if processed: st.toast(f"Processed {processed} auto-payments!", icon="🤖")
    st.session_state['scheduler_run'] = True

df_active = get_accounts(show_inactive=False)
account_map = dict(zip(df_active['name'], df_active['id']))
account_list = df_active['name'].tolist() if not df_active.empty else []
non_loan_accounts = df_active[df_active['type'] != 'Loan']['name'].tolist() if not df_active.empty else []

manual_due = get_due_manual_tasks()
if manual_due:
    st.warning(f"🔔 You have {len(manual_due)} manual transfers due!")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "📝 Entry", "🎯 Goals", "📅 Schedule", "⚙️ Settings"])

# --- TAB 1: OVERVIEW ---
with tab1:
    if manual_due:
        with st.expander(f"🔔 Pending Manual Transfers ({len(manual_due)})", expanded=True):
            for task in manual_due:
                c1, c2 = st.columns([4,1])
                c1.write(f"**{task['next_run_date']}:** {task['description']} (${task['amount']})")
                if c2.button("✅ Done", key=f"done_{task['id']}"):
                    cat = task.get('category', 'Recurring')
                    add_transaction(date.today(), task['amount'], f"✅ {task['description']}", 
                                  task['type'], task['from_account_id'], task['to_account_id'], cat, "Manual Scheduled")
                    
                    if task['frequency'] == 'Monthly':
                        next_date = datetime.strptime(task['next_run_date'], '%Y-%m-%d').date() + relativedelta(months=1)
                        supabase.table('schedule').update({"next_run_date": str(next_date)}).eq("id", task['id']).execute()
                    else:
                        supabase.table('schedule').delete().eq("id", task['id']).execute()
                    st.success("Recorded!")
                    clear_cache()
                    st.rerun()

    # --- NET WORTH & TREND ---
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

    # Trend Chart
    st.caption("📈 90-Day Net Worth Trend")
    trend_df = calculate_net_worth_trend(df_active)
    if not trend_df.empty:
        st.area_chart(trend_df.set_index('date')['net_worth'], height=200, color="#2E86C1")

    st.divider()
    
    # --- BUDGETS & SPENDING ---
    st.subheader("📊 Monthly Budgets")
    
    # Get expenses for current month
    today = date.today()
    start_month = today.replace(day=1)
    # End of month calc
    next_month = today.replace(day=28) + timedelta(days=4)
    end_month = next_month - timedelta(days=next_month.day)

    expenses = supabase.table('transactions').select("*") \
        .eq('type', 'Expense') \
        .gte('date', str(start_month)) \
        .lte('date', str(end_month)).execute().data
    
    # Get Categories with Budgets
    df_cats = get_categories("Expense")
    budgets = df_cats[df_cats['budget_limit'] > 0]
    
    if expenses:
        df_exp = pd.DataFrame(expenses)
        spent_sum = df_exp.groupby('category')['amount'].sum()
    else:
        spent_sum = pd.Series()
        
    # Display Progress Bars
    if not budgets.empty:
        for _, row in budgets.iterrows():
            cat_name = row['name']
            limit = row['budget_limit']
            spent = spent_sum.get(cat_name, 0.0)
            
            pct = min(spent / limit, 1.0)
            st.write(f"**{cat_name}** (${spent:,.0f} / ${limit:,.0f})")
            bar_color = "red" if spent > limit else "blue"
            st.progress(pct)
    else:
        st.info("No budgets set. Go to Settings > Edit Categories to add limits.")

# --- TAB 2: ENTRY ---
with tab2:
    st.subheader("New Transaction")
    
    # Simplified Types - "Custodial Out" is now just an Expense
    t_type = st.radio("Type", ["Expense", "Income", "Transfer", "Custodial In"], horizontal=True)
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        tx_date = c1.date_input("Date", datetime.today())
        
        # --- SPLIT PAYMENT LOGIC (Replaces old Custodial Out) ---
        is_split = False
        if t_type == "Expense":
            is_split = st.checkbox("🔀 Split Payment? (Pay from 2 sources)")
            
            if is_split:
                st.info("Split a single payment between two accounts (e.g. Bank + Cash)")
                col_a, col_b = st.columns(2)
                with col_a:
                    acc1 = st.selectbox("Source 1", non_loan_accounts, key="src1")
                    amt1 = st.number_input("Amount 1", min_value=0.0, format="%.2f")
                with col_b:
                    acc2 = st.selectbox("Source 2", non_loan_accounts, key="src2")
                    amt2 = st.number_input("Amount 2", min_value=0.0, format="%.2f")
                
                # Logic for Split: We treat it as 2 separate expense entries
                
            else:
                f_acc = st.selectbox("Paid From", non_loan_accounts)
                amt = c2.number_input("Amount", min_value=0.01)

        elif t_type in ["Income", "Refund"]:
            t_acc = st.selectbox("Deposit To", account_list)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type == "Transfer":
            c_a, c_b = st.columns(2)
            f_acc = c_a.selectbox("From", non_loan_accounts)
            t_acc = c_b.selectbox("To", account_list)
            amt = c2.number_input("Amount", min_value=0.01)

        elif t_type == "Custodial In":
            # Taking money from Custodial Account -> Bank
            c_a, c_b = st.columns(2)
            f_acc = c_a.selectbox("Custodial Source", df_active[df_active['type']=='Custodial']['name'])
            t_acc = c_b.selectbox("Bank Received", df_active[df_active['type']=='Bank']['name'])
            amt = c2.number_input("Amount", min_value=0.01)

        # Categories
        cat_type = "Income" if t_type == "Income" else "Expense"
        cat_options = get_categories(cat_type)['name'].tolist()
        category = st.selectbox("Category", cat_options)
        
        desc = st.text_input("Description")
        remark = st.text_area("Notes", height=68)
        
        if st.form_submit_button("Submit Transaction"):
            if t_type == "Expense" and is_split:
                if amt1 > 0:
                    add_transaction(tx_date, amt1, f"{desc} (Split 1)", "Expense", account_map[acc1], None, category, remark)
                if amt2 > 0:
                    add_transaction(tx_date, amt2, f"{desc} (Split 2)", "Expense", account_map[acc2], None, category, remark)
            else:
                # Standard
                f_id = account_map.get(f_acc) if 'f_acc' in locals() and f_acc else None
                t_id = account_map.get(t_acc) if 't_acc' in locals() and t_acc else None
                add_transaction(tx_date, amt, desc, t_type, f_id, t_id, category, remark)
            
            st.success("Saved!")
            clear_cache()

# --- TAB 3: GOALS (Sinking Funds) ---
with tab3:
    st.subheader("🎯 Sinking Funds Dashboard")
    goals = df_active[df_active['type'] == 'Sinking Fund']
    
    if not goals.empty:
        goals_calc = goals.copy()
        goals_calc['saved_sgd'] = goals_calc['balance'] * goals_calc['manual_exchange_rate']
        goals_calc['goal_sgd'] = goals_calc['goal_amount'] * goals_calc['manual_exchange_rate']
        
        total_saved = goals_calc['saved_sgd'].sum()
        total_goal = goals_calc['goal_sgd'].sum()
        grand_progress = min(total_saved / total_goal, 1.0) if total_goal > 0 else 0.0
            
        st.write("### 🏆 Total Progress")
        st.progress(grand_progress)
        st.caption(f"Saved: ${total_saved:,.0f} / ${total_goal:,.0f} (SGD)")
        
        st.divider()

        cols = st.columns(3)
        for i, (index, row) in enumerate(goals.iterrows()):
            with cols[i % 3]:
                st.write(f"**{row['name']}**")
                curr = row['currency']
                goal_amt = row['goal_amount'] or 1
                prog = min(row['balance'] / goal_amt, 1.0)
                st.progress(prog)
                st.caption(f"{curr} {row['balance']:,.0f} / {goal_amt:,.0f}")
    else:
        st.info("No Sinking Funds created yet.")

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
            
            s_cat = st.selectbox("Category", get_categories()['name'].tolist())
            s_manual = st.checkbox("🔔 Manual Reminder? (Tick if you do transfer manually)", value=False)
            s_type = st.selectbox("Type", ["Expense", "Transfer", "Income"])
            
            s_from, s_to = None, None
            if s_type == "Expense": s_from = st.selectbox("From Account", non_loan_accounts)
            elif s_type == "Income": s_to = st.selectbox("To Account", account_list)
            elif s_type == "Transfer":
                s_from = st.selectbox("From", non_loan_accounts, key="s_f")
                s_to = st.selectbox("To", account_list, key="s_t")
                
            if st.form_submit_button("Schedule It"):
                f_id = account_map.get(s_from)
                t_id = account_map.get(s_to)
                supabase.table('schedule').insert({
                    "description": s_desc, "amount": s_amount, "type": s_type, 
                    "from_account_id": f_id, "to_account_id": t_id, 
                    "frequency": s_freq, "next_run_date": str(s_date),
                    "is_manual": s_manual, "category": s_cat
                }).execute()
                st.success("Scheduled!")
                clear_cache()

    upcoming = supabase.table('schedule').select("*").order('next_run_date').execute().data
    if upcoming:
        st.write("### 🗓️ Upcoming Items")
        df_up = pd.DataFrame(upcoming)
        st.dataframe(df_up[['next_run_date', 'description', 'amount', 'frequency']], hide_index=True, use_container_width=True)
        
        with st.popover("🗑️ Delete Item"):
            del_id = st.number_input("ID to delete", step=1)
            if st.button("Delete"):
                supabase.table('schedule').delete().eq("id", del_id).execute()
                st.rerun()

# --- TAB 5: SETTINGS ---
with tab5:
    st.subheader("🔧 Configuration")
    
    # 1. CATEGORY EDITOR (New Request)
    st.write("### 🏷️ Edit Categories (Bulk)")
    st.info("Add new rows at the bottom. Delete by selecting rows.")
    
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
            update_table_direct('categories', edited_cats)
            st.success("Categories Updated!")

    st.divider()

    # 2. ACCOUNT EDITOR
    st.write("### 🏦 Edit Accounts")
    df_all_accounts = get_accounts(show_inactive=True)
    
    if not df_all_accounts.empty:
        edit_cols = ['name', 'balance', 'currency', 'manual_exchange_rate', 'remark', 'sort_order', 'is_active', 'include_net_worth', 'is_liquid_asset', 'id', 'type']
        
        edited_accs = st.data_editor(
            df_all_accounts[edit_cols], 
            key="account_editor",
            disabled=['id'],
            column_config={
                "is_active": st.column_config.CheckboxColumn("Active?", default=True),
                "sort_order": st.column_config.NumberColumn("Sort", min_value=1, max_value=999),
            }
        )
        
        if st.button("💾 Save Accounts"):
            update_table_direct('accounts', edited_accs)
            st.success("Accounts Updated!")
