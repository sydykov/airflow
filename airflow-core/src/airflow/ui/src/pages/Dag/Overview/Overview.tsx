/*!
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { Box, HStack, Skeleton } from "@chakra-ui/react";
import dayjs from "dayjs";
import { lazy, useState, Suspense } from "react";
import { useParams } from "react-router-dom";

import {
  useAssetServiceGetAssetEvents,
  useDagRunServiceGetDagRuns,
  useTaskInstanceServiceGetTaskInstances,
} from "openapi/queries";
import { AssetEvents } from "src/components/Assets/AssetEvents";
import { DurationChart } from "src/components/DurationChart";
import TimeRangeSelector from "src/components/TimeRangeSelector";
import { TrendCountButton } from "src/components/TrendCountButton";
import { isStatePending, useAutoRefresh } from "src/utils";

const FailedLogs = lazy(() => import("./FailedLogs"));

const defaultHour = "24";

export const Overview = () => {
  const { dagId } = useParams();

  const now = dayjs();
  const [startDate, setStartDate] = useState(now.subtract(Number(defaultHour), "hour").toISOString());
  const [endDate, setEndDate] = useState(now.toISOString());
  const [assetSortBy, setAssetSortBy] = useState("-timestamp");

  const refetchInterval = useAutoRefresh({});

  const { data: failedTasks, isLoading } = useTaskInstanceServiceGetTaskInstances({
    dagId: dagId ?? "",
    dagRunId: "~",
    orderBy: "-run_after",
    runAfterGte: startDate,
    runAfterLte: endDate,
    state: ["failed"],
  });

  const { data: failedRuns, isLoading: isLoadingFailedRuns } = useDagRunServiceGetDagRuns({
    dagId: dagId ?? "",
    runAfterGte: startDate,
    runAfterLte: endDate,
    state: ["failed"],
  });

  const { data: runs, isLoading: isLoadingRuns } = useDagRunServiceGetDagRuns(
    {
      dagId: dagId ?? "",
      limit: 14,
      orderBy: "-run_after",
    },
    undefined,
    {
      refetchInterval: (query) =>
        query.state.data?.dag_runs.some((run) => isStatePending(run.state)) ? refetchInterval : false,
    },
  );

  const { data: assetEventsData, isLoading: isLoadingAssetEvents } = useAssetServiceGetAssetEvents({
    limit: 6,
    orderBy: assetSortBy,
    sourceDagId: dagId,
    timestampGte: startDate,
    timestampLte: endDate,
  });

  return (
    <Box m={4}>
      <Box my={2}>
        <TimeRangeSelector
          defaultValue={defaultHour}
          endDate={endDate}
          setEndDate={setEndDate}
          setStartDate={setStartDate}
          startDate={startDate}
        />
      </Box>
      <HStack flexWrap="wrap">
        <TrendCountButton
          colorPalette="failed"
          count={failedTasks?.total_entries ?? 0}
          endDate={endDate}
          events={(failedTasks?.task_instances ?? []).map((ti) => ({
            timestamp: ti.start_date ?? ti.logical_date,
          }))}
          isLoading={isLoading}
          label="Failed Task"
          route={{
            pathname: "tasks",
            search: "state=failed",
          }}
          startDate={startDate}
        />
        <TrendCountButton
          colorPalette="failed"
          count={failedRuns?.total_entries ?? 0}
          endDate={endDate}
          events={(failedRuns?.dag_runs ?? []).map((dr) => ({
            timestamp: dr.run_after,
          }))}
          isLoading={isLoadingFailedRuns}
          label="Failed Run"
          route={{
            pathname: "runs",
            search: "state=failed",
          }}
          startDate={startDate}
        />
      </HStack>
      <HStack alignItems="flex-start" flexWrap="wrap">
        <Box borderRadius={4} borderStyle="solid" borderWidth={1} p={2} width="350px">
          {isLoadingRuns ? (
            <Skeleton height="200px" w="full" />
          ) : (
            <DurationChart entries={runs?.dag_runs.slice().reverse()} kind="Dag Run" />
          )}
        </Box>
        {assetEventsData && assetEventsData.total_entries > 0 ? (
          <AssetEvents
            data={assetEventsData}
            isLoading={isLoadingAssetEvents}
            setOrderBy={setAssetSortBy}
            title="Created Asset Event"
          />
        ) : undefined}
      </HStack>
      <Suspense fallback={<Skeleton height="100px" width="full" />}>
        <FailedLogs failedTasks={failedTasks} />
      </Suspense>
    </Box>
  );
};
