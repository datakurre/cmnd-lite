import base64
import json
import logging
import time
from functools import lru_cache
from typing import Optional

import apsw
import redis
from lxml import etree

logging.basicConfig(level=logging.DEBUG)

namespaces = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "zeebe": "http://camunda.org/schema/zeebe/1.0",
    "camunda": "http://camunda.org/schema/1.0/bpmn",
}


def get_bpmn(connection, process_definition_key):
    @lru_cache(maxsize=128)
    def get_bpmn_etree(key):
        cursor = connection.cursor()
        query = cursor.execute("SELECT resource FROM process WHERE key=?", (key,))
        for result in filter(bool, query.fetchone() or []):
            return etree.fromstring(base64.b64decode(result))

    return get_bpmn_etree(process_definition_key)


def maybe_str(x: int) -> Optional[str]:
    if x == -1:
        return None
    return str(x)


def init_db(connection):
    cursor = connection.cursor()
    cursor.execute(
        """\
create table if not exists process(
    key TEXT NOT NULL UNIQUE,
    bpmnProcessId TEXT NOT NULL,
    bpmnProcessName TEXT,
    version INTEGER NOT NULL,
    resourceName TEXT NOT NULL,
    resource TEXT NOT NULL,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (bpmnProcessId, version)
)
"""
    )
    cursor.execute(
        """\
create table if not exists form(
    key TEXT NOT NULL UNIQUE,
    processDefinition TEXT NOT NULL,
    schema TEXT NOT NULL,
    PRIMARY KEY (key, processDefinition),
    FOREIGN KEY (processDefinition) REFERENCES process(key)
)
"""
    )
    cursor.execute(
        """\
create table if not exists process_instance(
    key TEXT NOT NULL UNIQUE,
    processDefinition TEXT NOT NULL,
    parentProcessInstance TEXT,
    parentElementInstance TEXT,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    completed TEXT,
    PRIMARY KEY (key),
    FOREIGN KEY (processDefinition) REFERENCES process(key)
)
"""
    )
    cursor.execute(
        """\
create table if not exists element_instance(
    key TEXT NOT NULL UNIQUE,
    processInstance TEXT NOT NULL,
    elementId TEXT NOT NULL,
    elementName TEXT NOT NULL,
    bpmnElementType TEXT NOT NULL,
    flowScopeKey TEXT,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    completed TEXT,
    PRIMARY KEY (key),
    FOREIGN KEY (processInstance) REFERENCES process_instance(key)
)
"""
    )
    cursor.execute(
        """\
create table if not exists job(
    key TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    processInstance TEXT NOT NULL,
    processDefinition TEXT NOT NULL,
    elementInstance TEXT NOT NULL,
    customHeaders TEXT NOT NULL,
    variables TEXT NOT NULL,
    form TEXT,
    worker TEXT,
    errorCode TEXT,
    errorMessage TEXT,
    state TEXT NOT NULL,
    retryBackoff INTEGER NOT NULL,
    recurringTime INTEGER NOT NULL,
    retries INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    completed TEXT,
    PRIMARY KEY (key),
    FOREIGN KEY (processInstance) REFERENCES process_instance(key),
    FOREIGN KEY (processDefinition) REFERENCES process(key),
    FOREIGN KEY (elementInstance) REFERENCES element_instance(key),
    FOREIGN KEY (form, processDefinition) REFERENCES form(key, processDefinition)
)
"""
    )
    cursor.execute(
        """\
create table if not exists decision_requirements(
    key TEXT NOT NULL UNIQUE,
    decisionRequirementsId TEXT NOT NULL,
    decisionRequirementsName TEXT NOT NULL,
    version INTEGER NOT NULL,
    resource TEXT NOT NULL,
    resourceName TEXT NOT NULL,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (decisionRequirementsId, version)
)
"""
    )
    cursor.execute(
        """\
create table if not exists decision(
    key TEXT NOT NULL UNIQUE,
    decisionId TEXT NOT NULL,
    decisionName TEXT NOT NULL,
    decisionRequirements TEXT NOT NULL,
    version INTEGER NOT NULL,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (decisionId, version),
    FOREIGN KEY (decisionRequirements) REFERENCES decision_requirements(key)
)
"""
    )
    cursor.execute(
        """\
create table if not exists decision_evaluation(
    key TEXT NOT NULL UNIQUE,
    processInstance TEXT NOT NULL,
    processDefinition TEXT NOT NULL,
    elementInstance TEXT NOT NULL,
    decisionRequirements TEXT NOT NULL,
    decision TEXT NOT NULL,
    decisionOutput TEXT NOT NULL,
    evaluatedDecisions TEXT NOT NULL,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT,
    completed TEXT,
    PRIMARY KEY (key),
    FOREIGN KEY (processInstance) REFERENCES process_instance(key),
    FOREIGN KEY (processDefinition) REFERENCES process(key),
    FOREIGN KEY (elementInstance) REFERENCES element_instance(key),
    FOREIGN KEY (decisionRequirements) REFERENCES decision_requirements(key)
    FOREIGN KEY (decision) REFERENCES decision(key)
)
"""
    )
    cursor.execute(
        """\
create table if not exists variable(
    name TEXT NOT NULL,
    value TEXT,
    processInstance TEXT NOT NULL,
    processDefinition TEXT NOT NULL,
    flowScope TEXT,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    PRIMARY KEY (name, processInstance, flowScope),
    FOREIGN KEY(processInstance) REFERENCES process_instance(key),
    FOREIGN KEY(processDefinition) REFERENCES process(key),
    FOREIGN KEY(flowScope) REFERENCES element_instance(flowScopeKey)
)
"""
    )
    cursor.execute(
        """\
create table if not exists incident(
    key TEXT NOT NULL UNIQUE,
    job TEXT,
    processInstance TEXT NOT NULL,
    processDefinition TEXT NOT NULL,
    elementInstance TEXT NOT NULL,
    elementId TEXT NOT NULL,
    errorMessage TEXT,
    errorType TEXT NOT NULL,
    state TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    completed TEXT,
    PRIMARY KEY (key),
    FOREIGN KEY (job) REFERENCES job(key),
    FOREIGN KEY (processInstance) REFERENCES process_instance(key),
    FOREIGN KEY (elementInstance) REFERENCES element_instance(key)
)
"""
    )


def handle_process(connection, event):
    cursor = connection.cursor()
    cursor.execute(
        f"""\
INSERT INTO process VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET version=?, resourceName=?, resource=?, state=?, updated=?
WHERE updated <= excluded.updated
""",
        (
            str(event["key"]),
            event["value"]["bpmnProcessId"],
            "",
            event["value"]["version"],
            event["value"]["resourceName"],
            event["value"]["resource"],
            event["intent"],
            str(event["timestamp"]),
            str(event["timestamp"]),
            # ON CONFLICT
            event["value"]["version"],
            event["value"]["resourceName"],
            event["value"]["resource"],
            event["intent"],
            str(event["timestamp"]),
        ),
    )
    bpmn = get_bpmn(connection, str(event["value"]["processDefinitionKey"]))
    for el in bpmn.xpath("//bpmn:process", namespaces=namespaces) if bpmn else []:
        process_name = el.attrib.get("name")
        cursor.execute(
            "UPDATE process SET bpmnProcessName=? WHERE key=?",
            (
                process_name,
                str(event["value"]["processDefinitionKey"]),
            ),
        )
    for el in bpmn.xpath("//zeebe:userTaskForm", namespaces=namespaces) if bpmn else []:
        cursor.execute(
            f"""\
INSERT INTO form VALUES (?, ?, ?)
ON CONFLICT DO UPDATE SET schema=?
""",
            (
                el.attrib["id"],
                str(event["value"]["processDefinitionKey"]),
                el.text,
                # ON CONFLICT
                el.text,
            ),
        )


def handle_process_instance(connection, event):
    cursor = connection.cursor()
    if event["value"]["elementId"] == event["value"]["bpmnProcessId"]:
        cursor.execute(
            f"""\
INSERT INTO process_instance VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET state=?, updated=?, completed=?
WHERE updated <= excluded.updated
""",
            (
                str(event["key"]),
                str(event["value"]["processDefinitionKey"]),
                maybe_str(event["value"]["parentProcessInstanceKey"]),
                maybe_str(event["value"]["parentElementInstanceKey"]),
                event["intent"],
                str(event["timestamp"]),
                str(event["timestamp"]),
                event["intent"] == "ELEMENT_COMPLETED"
                and str(event["timestamp"])
                or None,
                # ON CONFLICT
                event["intent"],
                str(event["timestamp"]),
                event["intent"] == "ELEMENT_COMPLETED"
                and str(event["timestamp"])
                or None,
            ),
        )
    if event["value"]["bpmnElementType"] not in ["SEQUENCE_FLOW", "PROCESS"]:
        cursor.execute(
            f"""\
INSERT INTO element_instance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET flowScopeKey=?, state=?, updated=?, completed=?
WHERE updated <= excluded.updated
""",
            (
                str(event["key"]),
                str(event["value"]["processInstanceKey"]),
                event["value"]["elementId"],
                "",
                event["value"]["bpmnElementType"],
                maybe_str(event["value"]["flowScopeKey"]) or str(event["key"]),
                event["intent"],
                str(event["timestamp"]),
                str(event["timestamp"]),
                event["intent"] in ["ELEMENT_COMPLETED", "SEQUENCE_FLOW_TAKEN"]
                and str(event["timestamp"])
                or None,
                # ON CONFLICT
                maybe_str(event["value"]["flowScopeKey"]) or str(event["key"]),
                event["intent"],
                str(event["timestamp"]),
                event["intent"] in ["ELEMENT_COMPLETED", "SEQUENCE_FLOW_TAKEN"]
                and str(event["timestamp"])
                or None,
            ),
        )
    bpmn = get_bpmn(connection, str(event["value"]["processDefinitionKey"]))
    element_id = event["value"]["elementId"]
    for el in bpmn.xpath(f'//*[@id="{element_id}"]') if bpmn else []:
        element_name = el.attrib.get("name")
        if element_name:
            cursor.execute(
                "UPDATE element_instance SET elementName =? WHERE key=?",
                (
                    element_name,
                    str(event["key"]),
                ),
            )


def handle_job(connection, event):
    cursor = connection.cursor()
    form = (
        event["value"]["customHeaders"].get("io.camunda.zeebe:formKey") or ""
    ).rsplit(":", 1)[-1] or None
    cursor.execute(
        f"""\
INSERT INTO job VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET variables=?, worker=?, errorCode=?, errorMessage=?, state=?, retryBackoff=?, recurringTime=?, retries=?, deadline=?, updated=?, completed=?
WHERE updated <= excluded.updated
""",
        (
            str(event["key"]),
            event["value"]["type"],
            str(event["value"]["processInstanceKey"]),
            str(event["value"]["processDefinitionKey"]),
            str(event["value"]["elementInstanceKey"]),
            json.dumps(event["value"]["customHeaders"]),
            json.dumps(event["value"]["variables"]),
            form,
            event["value"]["worker"],
            event["value"]["errorCode"],
            event["value"]["errorMessage"],
            event["intent"],
            event["value"]["retryBackoff"],
            event["value"]["recurringTime"],
            event["value"]["retries"],
            event["value"]["deadline"],
            event["timestamp"],
            event["timestamp"],
            event["intent"] == "COMPLETED" and event["timestamp"] or None,
            # ON CONFLICT
            json.dumps(event["value"]["variables"]),
            event["value"]["worker"],
            event["value"]["errorCode"],
            event["value"]["errorMessage"],
            event["intent"],
            event["value"]["retryBackoff"],
            event["value"]["recurringTime"],
            event["value"]["retries"],
            event["value"]["deadline"],
            event["timestamp"],
            event["intent"] == "COMPLETED" and event["timestamp"] or None,
        ),
    )


def handle_decision_requirements(connection, event):
    cursor = connection.cursor()
    cursor.execute(
        f"""\
INSERT INTO decision_requirements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET decisionRequirementsId=?, decisionRequirementsName=?, version=?, resource=?, resourceName=?, state=?, updated=?
WHERE updated <= excluded.updated
""",
        (
            str(event["key"]),
            event["value"]["decisionRequirementsId"],
            event["value"]["decisionRequirementsName"],
            event["value"]["decisionRequirementsVersion"],
            event["value"]["resource"],
            event["value"]["resourceName"],
            event["intent"],
            str(event["timestamp"]),
            str(event["timestamp"]),
            # ON CONFLICT
            event["value"]["decisionRequirementsId"],
            event["value"]["decisionRequirementsName"],
            event["value"]["decisionRequirementsVersion"],
            event["value"]["resource"],
            event["value"]["resourceName"],
            event["intent"],
            str(event["timestamp"]),
        ),
    )


def handle_decision(connection, event):
    cursor = connection.cursor()
    cursor.execute(
        f"""\
INSERT INTO decision VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET decisionId=?, decisionName=?, decisionRequirements=?, version=?, state=?, updated=?
WHERE updated <= excluded.updated
""",
        (
            str(event["key"]),
            event["value"]["decisionId"],
            event["value"]["decisionName"],
            event["value"]["decisionRequirementsKey"],
            event["value"]["version"],
            event["intent"],
            str(event["timestamp"]),
            str(event["timestamp"]),
            # ON CONFLICT
            event["value"]["decisionId"],
            event["value"]["decisionName"],
            event["value"]["decisionRequirementsKey"],
            event["value"]["version"],
            event["intent"],
            str(event["timestamp"]),
        ),
    )


def handle_decision_evaluation(connection, event):
    cursor = connection.cursor()
    cursor.execute(
        f"""\
INSERT INTO decision_evaluation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET decisionOutput=?, evaluatedDecisions=?, state=?, updated=?, completed=?
WHERE updated <= excluded.updated
""",
        (
            str(event["key"]),
            str(event["value"]["processInstanceKey"]),
            str(event["value"]["processDefinitionKey"]),
            str(event["value"]["elementInstanceKey"]),
            event["value"]["decisionRequirementsKey"],
            event["value"]["decisionKey"],
            json.dumps(event["value"]["decisionOutput"]),
            json.dumps(event["value"]["evaluatedDecisions"]),
            event["intent"],
            str(event["timestamp"]),
            str(event["timestamp"]),
            event["intent"] == "EVALUATED" and event["timestamp"] or None,
            # ON CONFLICT
            json.dumps(event["value"]["decisionOutput"]),
            json.dumps(event["value"]["evaluatedDecisions"]),
            event["intent"],
            str(event["timestamp"]),
            event["intent"] == "EVALUATED" and event["timestamp"] or None,
        ),
    )


def handle_variable(connection, event):
    cursor = connection.cursor()
    cursor.execute(
        f"""\
INSERT INTO variable VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET value=?, state=?, updated=?
WHERE updated <= excluded.updated
""",
        (
            event["value"]["name"],
            event["value"]["value"],
            str(event["value"]["processInstanceKey"]),
            str(event["value"]["processDefinitionKey"]),
            maybe_str(event["value"]["scopeKey"])
            if str(event["value"]["scopeKey"])
            != str(event["value"]["processInstanceKey"])
            else None,
            event["intent"],
            str(event["timestamp"]),
            str(event["timestamp"]),
            # ON CONFLICT
            event["value"]["name"],
            event["intent"],
            str(event["timestamp"]),
        ),
    )


def handle_incident(connection, event):
    cursor = connection.cursor()
    cursor.execute(
        f"""\
INSERT INTO incident VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO UPDATE SET errorMessage=?, errorType=?, state=?, updated=?, completed=?
WHERE updated <= excluded.updated
""",
        (
            event["key"],
            maybe_str(event["value"]["jobKey"]),
            str(event["value"]["processInstanceKey"]),
            str(event["value"]["processDefinitionKey"]),
            str(event["value"]["elementInstanceKey"]),
            event["value"]["elementId"],
            # maybe_str(event["value"]["variableScopeKey"]),
            event["value"]["errorMessage"],
            event["value"]["errorType"],
            event["intent"],
            str(event["timestamp"]),
            str(event["timestamp"]),
            event["intent"] == "RESOLVED" and event["timestamp"] or None,
            # ON CONFLICT
            event["value"]["errorMessage"],
            event["value"]["errorType"],
            event["intent"],
            str(event["timestamp"]),
            event["intent"] == "RESOLVED" and event["timestamp"] or None,
        ),
    )


def handle_zeebe_event(connection, event_type, event_id, event):
    if event_type == "process":
        handle_process(connection, event)
    elif event_type == "process_instance":
        handle_process_instance(connection, event)
    elif event_type == "job":
        handle_job(connection, event)
    elif event_type == "decision_requirements":
        handle_decision_requirements(connection, event)
    elif event_type == "decision":
        handle_decision(connection, event)
    elif event_type == "decision_evaluation":
        handle_decision_evaluation(connection, event)
    elif event_type == "variable":
        handle_variable(connection, event)
    elif event_type == "incident":
        handle_incident(connection, event)


def main():
    STREAMS = {
        b"zeebe:DECISION": 0,
        b"zeebe:DECISION_EVALUATION": 0,
        b"zeebe:DECISION_REQUIREMENTS": 0,
        b"zeebe:INCIDENT": 0,
        b"zeebe:JOB": 0,
        b"zeebe:PROCESS": 0,
        b"zeebe:PROCESS_INSTANCE": 0,
        b"zeebe:VARIABLE": 0,
    }
    logging.debug(STREAMS)
    try:
        connection = apsw.Connection("dbfile")
        init_db(connection)
    finally:
        connection.close()

    while True:
        try:
            r = redis.Redis(host="localhost", port=6379, db=0)
            logging.info("Connected.")
            while True:
                for stream_data in r.xread(STREAMS, block=30 * 1000):
                    stream_name, stream_items = stream_data
                    zeebe_event_type = (
                        stream_name.decode("utf-8").split(":", 1)[-1].lower()
                    )
                    for stream_item in stream_items:
                        stream_event_id, zeebe_events = stream_item
                        for zeebe_event_id, zeebe_event in zeebe_events.items():
                            zeebe_event = json.loads(zeebe_event)
                            if zeebe_event.get("recordType") != "EVENT":
                                continue
                            while True:
                                try:
                                    connection = apsw.Connection("dbfile")
                                    handle_zeebe_event(
                                        connection,
                                        zeebe_event_type,
                                        zeebe_event_id,
                                        zeebe_event,
                                    )
                                    break
                                finally:
                                    try:
                                        connection.close()
                                    except:
                                        pass
                        STREAMS[stream_name] = stream_event_id
        except Exception as e:
            logging.exception(e)
        finally:
            try:
                r.close()
            except:
                pass
            logging.info("Reconnecting in 10 seconds.")
            time.sleep(10)


if __name__ == "__main__":
    main()
