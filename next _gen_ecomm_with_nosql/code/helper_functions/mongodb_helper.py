import csv
import numpy as np
import pandas as pd
import pymongo
import json
import pprint

class mongodb_helper:
    """
    The class contains reusable code for mongodb database call.
    """
    def __init__(self, mongo_url):
        """This constructor method sets up Mongodb connection"""
        self.mongo = pymongo.MongoClient(mongo_url)
        self.database_name = ""
        self.collection_name = ""
    
    def create_database(self, db_name):
        """This method creates Mongodb database"""
        self.database_name = self.mongo[db_name]
        
    def create_collection_in_database(self, db_name, collection_name):
        """This method creates a collection (table) in Mongodb database"""
        database_name = self.database_name
        collection_name_string = '"' + collection_name + '"'
        self.collection_name = database_name[collection_name_string]
        
    def drop_collection_in_database(self, db_name, collection_name):
        """This method drops a collection (table) in Mongodb database"""
        database_name = self.database_name
        collection_name = self.collection_name
        database_name.collection_name.drop()
    
    def execute_command(self, command_string):
        """This method executes a command"""
        inserted_documents = database_name.collection_name_value.insert_many(json_data_list)
        print(inserted_documents)
        
    def load_json_data_in_collection(self, json_data_list, db_name = "", collection_name = ""):
        """This method loads json data in a Mongodb collection"""
        if db_name == "" and collection_name == "":
            database_name = self.database_name
            collection_name = self.collection_name
        else:
            database_name = db_name
            collection_name_value = collection_name
        
        print(database_name, collection_name)
        command_string = database_name + "." + collection_name_value + "(json_data_list)"
        print(command_string)
        #execute_command(command_string)
        
    def load_csv_data_in_collection(self, csv_file, db_name = "", collection_name = ""):
        """This method loads json data in a Mongodb collection"""
        f_csv_file = open(csv_file, 'r')
        csv_reader = csv.DictReader(f_csv_file)
        
        df = pd.read_csv(csv_file)
        print("Number of records :", len(df))
        header = df.columns[1:]
        json_list = []
        for rec in csv_reader:
            json_list.append(rec)
            
        self.load_json_data_in_collection(json_list, db_name, collection_name)
    
    def find_data_in_collection(self, db_name = "", collection_name = "", filter = ""):
        """This method finds data in a Mongodb collection"""
        if db_name == "" and collection_name == "":
            database_name = self.database_name
            collection_name = self.collection_name
        else:
            database_name = db_name
            collection_name = collection_name
        
        if filter == "":
            data = database_name.collection_name.find()
        else:    
            data = database_name.collection_name.find(filter)
        for rec in data:
            print(data)