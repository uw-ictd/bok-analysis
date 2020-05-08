""" Computing active and registered users on the network over time
"""

import altair
import bok.dask_infra
import dask.config
import dask.dataframe
import dask.distributed
import datetime
import math
import numpy as np
import pandas as pd

# Configs
day_intervals = 7
# IMPORTANT: Run get_data_range() to update these values when loading in a new dataset!
max_date = datetime.datetime.strptime('2020-02-13 21:29:54', '%Y-%m-%d %H:%M:%S')

def cohort_as_date_interval(x):
    cohort_start = max_date - datetime.timedelta(day_intervals * x)
    cohort_end = max_date - datetime.timedelta(day_intervals * x + day_intervals - 1)

    return cohort_start.strftime("%Y/%m/%d") + "-" + cohort_end.strftime("%Y/%m/%d")

def get_cohort(x):
    return x["start"].apply(lambda x_1: (max_date - x_1).days // day_intervals, meta=('start', 'int64'))

def get_date(x):
    return x["cohort"].apply(cohort_as_date_interval, meta=('cohort', 'object'))

def get_active_users_query(flows):
    # Make indexes a column and select "start" and "user" columns
    query = flows.reset_index()[["start", "user"]]
    # Map each start to a cohort
    query = query.assign(cohort=get_cohort)
    # Group by cohorts and get the all the users
    query = query.groupby("cohort")["user"]
    # Count the number of unique users per cohort
    query = query.nunique()
    # Rename the column names to be standardized
    query = query.rename({0: "user"})
    # Convert to dataframe
    query = query.to_frame()
    # Get the cohort column back
    query = query.reset_index()

    return query

def get_registered_users_query(transactions):
    # Set down the types for the dataframe
    types = {
        'start': 'datetime64',
        "action": "object",
        "user": "object",
        "amount": "int64",
        "price": "int64"
    }

    # Update the types in the dataframe
    query = transactions.astype(types)
    # Map each start to a cohort
    query = query.assign(cohort=get_cohort)
    # Group by cohorts and get the all the users
    query = query.groupby("cohort")["user"]
    # Count the number of unique users per cohort
    query = query.nunique()
    # Reverse the array and ignore cohorts that are past the max date
    query = query.reset_index()
    query["cohort"] = query["cohort"] * -1
    query = query.query("cohort <= 0")
    query = query.set_index("cohort")
    # Get the cumulative sum of users over time
    query = query.cumsum()
    # Re-reverse the dataframe
    query = query.reset_index()
    query["cohort"] = query["cohort"] * -1

    return query

def get_user_data(flows, transactions):
    active_users = get_active_users_query(flows)
    registered_users = get_registered_users_query(transactions)

    # Join the active and registered users together
    users = active_users.merge(registered_users, left_on="cohort", right_on="cohort", suffixes=('_active', '_registered'))
    # Map each cohort to a date
    users = users.assign(date_range=get_date)

    return users

if __name__ == "__main__":
    client = bok.dask_infra.setup_dask_client()

    # Import the flows dataset
    #
    # Importantly, dask is lazy and doesn't actually import the whole thing,
    # but just keeps track of where the file shards live on disk.

    flows = dask.dataframe.read_parquet("data/clean/flows", engine="pyarrow")
    length = len(flows)
    transactions = dask.dataframe.read_csv("data/clean/first_time_user_transactions.csv")
    print("To see execution status, check out the dask status page at localhost:8787 while the computation is running.")
    print("Processing {} flows".format(length))

    # Get the user data
    users = get_user_data(flows, transactions)
    # Get the data in a form that is easily plottable
    users = users.melt(id_vars=["date_range"], value_vars=["user_active", "user_registered"], var_name="user_type", value_name="num_users")
    # Reset the types of the dataframe
    types = {
        "date_range": "object",
        "user_type": "category",
        "num_users": "int64"
    }
    users = users.astype(types)
    # Compute the query
    users = users.compute()

    altair.Chart(users).mark_line().encode(
        x="date_range",
        y="num_users",
        color="user_type",
    ).serve()
    

# Gets the start and end of the date in the dataset. 
def get_date_range():
    # ------------------------------------------------
    # Dask tuning, currently set for a 8GB RAM laptop
    # ------------------------------------------------

    # Compression sounds nice, but results in spikes on decompression
    # that can lead to unstable RAM use and overflow.
    dask.config.set({"dataframe.shuffle-compression": False})
    dask.config.set({"distributed.scheduler.allowed-failures": 50})
    dask.config.set({"distributed.scheduler.work-stealing": True})

    # Aggressively write to disk but don't kill worker processes if
    # they stray. With a small number of workers each worker killed is
    # big loss. The OOM killer will take care of the overall system.
    dask.config.set({"distributed.worker.memory.target": 0.2})
    dask.config.set({"distributed.worker.memory.spill": 0.4})
    dask.config.set({"distributed.worker.memory.pause": 0.6})
    dask.config.set({"distributed.worker.memory.terminate": False})

    # The memory limit parameter is undocumented and applies to each worker.
    cluster = dask.distributed.LocalCluster(n_workers=2,
                                            threads_per_worker=1,
                                            memory_limit='2GB')
    client = dask.distributed.Client(cluster)

    # Import the flows dataset
    #
    # Importantly, dask is lazy and doesn't actually import the whole thing,
    # but just keeps track of where the file shards live on disk.

    flows = dask.dataframe.read_parquet("data/clean/flows", engine="pyarrow")
    length = len(flows)
    print("To see execution status, check out the dask status page at localhost:8787 while the computation is running.")
    print("Processing {} flows".format(length))

    # Gets the max date in the flows dataset
    max_date = flows.reset_index()["start"].max()
    max_date = max_date.compute()
    print("max date: ", max_date)