import math
import numpy as np
import pandas as pd
import psycopg2

class postgress_helper:
    """
    The class contains reusable code for Postgress database call.
    """
    
    def __init__(self, db_name):
        self.postgress_connection = psycopg2.connect(
                                   user = "postgres",
                                   password = "ucb",
                                   host = "postgres",
                                   port = "5432",
                                   database = db_name
                                  )
    
    
    """
    Function to run a select query and return rows in a pandas dataframe
    pandas puts all numeric values from postgres to float
    if it will fit in an integer, change it to integer
    """
    def select_query_pandas(self, query, rollback_before_flag = True, rollback_after_flag = True):
        "function to run a select query and return rows in a pandas dataframe"
        
        if rollback_before_flag:
            self.postgress_connection.rollback()
        
        df = pd.read_sql_query(query, self.postgress_connection)
        
        if rollback_after_flag:
            self.postgress_connection.rollback()
        
        # fix the float columns that really should be integers
        
        for column in df:
            
            if df[column].dtype == "float64":
                
                fraction_flag = False
                
                for value in df[column].values:
                    
                    if not np.isnan(value):
                        if value - math.floor(value) != 0:
                            fraction_flag = True
                
                if not fraction_flag:
                    df[column] = df[column].astype('Int64')
        
        return(df)


