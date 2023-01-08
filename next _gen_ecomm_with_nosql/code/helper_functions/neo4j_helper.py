import neo4j
import csv
import math
import numpy as np
import pandas as pd

class neo4j_helper:
    """
    The class contains reusable code for neo4j database call.
    """
    
    def __init__(self, userid, password, db_name):
        self.neo4j_driver = neo4j.GraphDatabase.driver(uri="neo4j://neo4j:7687", auth=(userid,password))
        self.neo4j_session = self.neo4j_driver.session(database = db_name)
    
    def neo4j_wipe_out_database(self):
        "Wipe out database by deleting all nodes and relationships"
        
        query = "match (node)-[relationship]->() delete node, relationship"
        self.neo4j_session.run(query)
        
        query = "match (node) delete node"
        self.neo4j_session.run(query)
    
    def neo4j_run_query_pandas(self, query, **kwargs):
        "Run a query and return the results in a pandas dataframe"
        
        result = self.neo4j_session.run(query, **kwargs)
        
        df = pd.DataFrame([r.values() for r in result], columns=result.keys())
        return df
    
    def neo4j_number_nodes_relationships(self):
        "Print the number of nodes and relationships"
        
        query = """
        match (n) 
        return n.name as node_name, labels(n) as labels
        order by n.name
        """

        df = self.neo4j_run_query_pandas(query)
        
        number_nodes = df.shape[0]
        query = """
        match (n1)-[r]->(n2) 
        return n1.name as node_name_1, labels(n1) as node_1_labels, 
            type(r) as relationship_type, n2.name as node_name_2, labels(n2) as node_2_labels
        order by node_name_1, node_name_2
        """
        
        df = self.neo4j_run_query_pandas(query)
        
        number_relationships = df.shape[0]
        
        print("-------------------------")
        print("  Nodes:", number_nodes)
        print("  Relationships:", number_relationships)
        print("-------------------------")
    
    """
    CREATE (john:Person {name: 'John'})
    CREATE (joe:Person {name: 'Joe'})
    CREATE (steve:Person {name: 'Steve'})
    CREATE (sara:Person {name: 'Sara'})
    CREATE (maria:Person {name: 'Maria'})
    CREATE (john)-[:FRIEND]->(joe)-[:FRIEND]->(steve)
    CREATE (john)-[:FRIEND]->(sara)-[:FRIEND]->(maria)
    """
    def neo4j_create_node(self, label_details, node_property):
        """create a node with label"""
        query = f"MERGE ({label_details} {node_property} )"
        self.neo4j_session.run(query
                             , label_details = label_details
                             , node_property = node_property)

    def neo4j_create_relationship_one_way(self
                                        , from_label_name
                                        , to_label_name
                                        , from_node
                                        , to_node
                                        , weight
                                        , from_property_name_value
                                        , to_property_name_value
                                        , relationship_name):
        """Create a relationship one way between two nodes with a weight.
        Ex.
        MATCH (id_21:product)
            , (id_64:product)
        WHERE id_21.name = "Sir Rodney's Scones" 
        AND   id_64.name = "Wimmers gute Semmelknödel"
        CREATE (id_21)-[: BOUGHT_TOGETHER]->(id_64)ß
        RETURN id_21
             , id_64
        """
        
        #query = """CREATE (from)-[:BOUGHT_TOGETHER {weight: $weight}]->(to)"""
        query_1 = f"MATCH ({from_node}:{from_label_name}), \n({to_node}:{to_label_name}) "
        query_2 = f'\nWHERE {from_node}.name = "' + from_property_name_value + '"'
        query_3 = f'\n AND {to_node}.name = "' + to_property_name_value + '" '
        query_weight = "{" + f"weight: {weight}" + "}]"
        query_4 = f"\nCREATE ({from_node})-[:{relationship_name} " + query_weight
        query_5 = f"->({to_node}) "
        query_6 = f"\nRETURN {from_node}, {to_node}"
        query = query_1 + query_2 + query_3 + query_4 + query_5 + query_6
        
        self.neo4j_session.run(query
                             , label_name = label_name
                             , from_node = from_node
                             , to_node = to_node
                             , weight = weight
                             , from_property_name_value = from_property_name_value
                             , to_property_name_value = to_property_name_value
                             , relationship_name = relationship_name)
    
    def neo4j_build_relationship_one_way(self
                                       , from_label_value
                                       , from_label_name
                                       , to_label_value
                                       , to_label_name
                                       , where_clause
                                       , relationship_name):
        """Create a relationship one way between two nodes with a weight.
        Ex.
        MATCH (id_21:product)
            , (id_64:product)
        WHERE id_21.name = "Sir Rodney's Scones" 
        AND   id_64.name = "Wimmers gute Semmelknödel"
        CREATE (id_21)-[: BOUGHT_TOGETHER]->(id_64)
        RETURN id_21
             , id_64
        """
        
        #query = """CREATE (from)-[:BOUGHT_TOGETHER {weight: $weight}]->(to)"""
        query_1 = f"MATCH ({from_label_value}:{from_label_name}), \n({to_label_value}:{to_label_name}) "
        query_2 = where_clause
        query_3 = f"\nCREATE ({from_label_value})-[:{relationship_name}]->({to_label_value}) "
        query_4 = f"\nRETURN {from_label_value}, {to_label_value}"
        query = query_1 + query_2 + query_3 + query_4
        
        self.neo4j_session.run(query
                             , from_label_value = from_label_value
                             , from_label_name = from_label_name
                             , to_label_value = to_label_value
                             , to_label_name = to_label_name
                             , where_clause = where_clause
                             , relationship_name = relationship_name)
        
    def neo4j_create_relationship_two_way(self
                                        , from_label_name
                                        , to_label_name
                                        , from_node
                                        , to_node
                                        , weight
                                        , from_property_name_value
                                        , to_property_name_value
                                        , relationship_name):
        """Create a relationship one way between two nodes with a weight.
        Ex.
        MATCH (id_21:product)
            , (id_64:product)
        WHERE id_21.name = "Sir Rodney's Scones" 
        AND   id_64.name = "Wimmers gute Semmelknödel"
        CREATE (id_21)-[: BOUGHT_TOGETHER]->(id_64)-[: BOUGHT_TOGETHER]->(id_21)
        RETURN id_21
             , id_64
        """
        
        #query = """CREATE (from)-[:BOUGHT_TOGETHER {weight: $weight}]->(to)"""
        query_1 = f"MATCH ({from_node}:{from_label_name}), \n({to_node}:{to_label_name}) "
        query_2 = f'\nWHERE {from_node}.name = "' + from_property_name_value + '"'
        query_3 = f'\n AND {to_node}.name = "' + to_property_name_value + '" '
        query_weight = "{" + f"weight: {weight}" + "}]"
        query_4 = f"\nCREATE ({from_node})-[:{relationship_name} " + query_weight
        query_5 = f"->({to_node})-[:{relationship_name} " + query_weight + f"->({from_node}) "
        query_6 = f"\nRETURN {from_node}, {to_node}"
        query = query_1 + query_2 + query_3 + query_4 + query_5 + query_6
        
        self.neo4j_session.run(query
                             , label_name = label_name
                             , from_node = from_node
                             , to_node = to_node
                             , weight = weight
                             , from_property_name_value = from_property_name_value
                             , to_property_name_value = to_property_name_value
                             , relationship_name = relationship_name)
        

